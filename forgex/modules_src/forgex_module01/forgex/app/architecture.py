"""Static architecture mapping: imports, routes, models, components, services, tests."""
from __future__ import annotations

import ast
import posixpath
import re
import warnings
from collections import defaultdict
from typing import Any

from . import config
from .context import Ctx
from .evidence import snip
from .infra import parse_compose

CODE_LANGS = {"Python", "JavaScript", "TypeScript", "Java", "Vue"}
JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".vue")
DB_LIBS = {
    "sqlalchemy": "SQLAlchemy", "psycopg2": "PostgreSQL driver", "psycopg": "PostgreSQL driver",
    "asyncpg": "PostgreSQL driver", "sqlite3": "SQLite", "pymongo": "MongoDB driver", "pymysql": "MySQL driver",
    "django.db": "Django ORM", "redis": "Redis client", "peewee": "Peewee ORM", "pg": "PostgreSQL driver",
    "mysql2": "MySQL driver", "mongoose": "Mongoose", "@prisma/client": "Prisma", "typeorm": "TypeORM",
    "sequelize": "Sequelize", "knex": "Knex", "javax.persistence": "JPA", "jakarta.persistence": "JPA",
    "org.springframework.data": "Spring Data", "java.sql": "JDBC",
}
FRONT = {"React", "Next.js", "Vue", "Angular", "Svelte", "Vite"}
BACK = {"FastAPI", "Django", "Flask", "Express", "NestJS", "Spring Boot", "Laravel", "Symfony", "Gin", "Echo", "Fiber"}

PY_ROUTE = re.compile(r"""^\s*@\s*([\w.]+)\.(get|post|put|delete|patch|options|head)\(\s*(?:path\s*=\s*)?[rbf]*["']([^"']*)["']""")
PY_FLASK = re.compile(r"""^\s*@\s*([\w.]+)\.route\(\s*[rf]*["']([^"']*)["'](?:.*methods\s*=\s*\[([^\]]*)\])?""")
DJ_PATH = re.compile(r"""\b(?:re_)?path\(\s*r?["']([^"']*)["']\s*,\s*([\w.]+)""")
JS_ROUTE = re.compile(r"""\b(?:app|router|\w*[Rr]outer|server|api)\.(get|post|put|delete|patch|all)\(\s*["'`]([^"'`]+)["'`]""")
NEST_METHOD = re.compile(r"""^\s*@(Get|Post|Put|Delete|Patch)\(\s*(?:["']([^"']*)["'])?""")
NEST_CTRL = re.compile(r"""@Controller\(\s*(?:["']([^"']*)["'])?""")
JAVA_MAP = re.compile(r"""@(Get|Post|Put|Delete|Patch|Request)Mapping\b(?:\(\s*(?:(?:value|path)\s*=\s*)?\{?\s*"([^"]*)")?""")
JS_IMPORT = re.compile(r"""\bfrom\s+['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\)|^\s*import\s+['"]([^'"]+)['"]|import\(\s*['"]([^'"]+)['"]\s*\)""")
JS_API_CALL = re.compile(r"""\b(?:fetch|axios(?:\.\w+)?|\$http\.\w+)\(\s*[`'"]([^`'"]*)""")
PY_CLASS = re.compile(r"^class\s+(\w+)\s*\(([^)]*)\)\s*:")
SQL_TABLE = re.compile(r"""CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"\[]?([\w.]+)""", re.I)


def _join(*parts: str) -> str:
    out = "/".join(p.strip("/") for p in parts if p and p.strip("/"))
    return "/" + out


class PyIndex:
    def __init__(self, paths: list[str]):
        self.map: dict[str, set[str]] = defaultdict(set)
        initdirs = {posixpath.dirname(p) for p in paths if posixpath.basename(p) == "__init__.py"}
        for p in paths:
            parts = p[:-3].split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if not parts:
                continue
            self.map[".".join(parts)].add(p)
            if parts[0] == "src" and len(parts) > 1:
                self.map[".".join(parts[1:])].add(p)
            cur = posixpath.dirname(p)
            while cur and cur in initdirs:
                cur = posixpath.dirname(cur)
            rel = p[len(cur) + 1:] if cur else p
            rp = rel[:-3].split("/")
            if rp[-1] == "__init__":
                rp = rp[:-1]
            if rp:
                self.map[".".join(rp)].add(p)

    @staticmethod
    def _pick(c: set[str], importer: str) -> str:
        top = importer.split("/")[0]
        return min(c, key=lambda x: (x.split("/")[0] != top, len(x), x))

    def exact(self, dotted: str, importer: str) -> str | None:
        c = self.map.get(dotted)
        return self._pick(c, importer) if c else None

    def resolve(self, dotted: str, importer: str) -> str | None:
        parts = dotted.split(".")
        for n in range(len(parts), 0, -1):
            r = self.exact(".".join(parts[:n]), importer)
            if r:
                return r
        return None


def _py_edges(index: PyIndex, tree: ast.AST, importer: str):
    edges, external = [], []
    pkg = [x for x in posixpath.dirname(importer).split("/") if x]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                r = index.resolve(a.name, importer)
                (edges if r else external).append((r, node.lineno) if r else (a.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:
                keep = len(pkg) - (node.level - 1)
                if keep < 0:
                    continue
                base = pkg[:keep] + (mod.split(".") if mod else [])
                dotted = ".".join(base)
                hit = False
                for a in node.names:
                    r = index.exact(f"{dotted}.{a.name}" if dotted else a.name, importer)
                    if r:
                        edges.append((r, node.lineno)); hit = True
                if not hit and dotted:
                    r = index.exact(dotted, importer)
                    if r:
                        edges.append((r, node.lineno))
            else:
                hit = False
                for a in node.names:
                    r = index.exact(f"{mod}.{a.name}", importer) if mod else None
                    if r:
                        edges.append((r, node.lineno)); hit = True
                if not hit:
                    r = index.resolve(mod, importer) if mod else None
                    if r:
                        edges.append((r, node.lineno))
                    elif mod:
                        external.append((mod, node.lineno))
    return [(d, l) for d, l in edges if d != importer], external


def _js_resolve(spec: str, importer: str, paths: set[str]) -> str | None:
    bases = []
    if spec.startswith("."):
        bases.append(posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec)))
    elif spec.startswith(("@/", "~/")):
        d = posixpath.dirname(importer)
        while True:
            if d.split("/")[-1] == "src":
                bases.append(posixpath.join(d, spec[2:]))
                break
            if not d:
                break
            d = posixpath.dirname(d)
    for b in bases:
        cands = [b] + [b + e for e in JS_EXTS] + [f"{b}/index{e}" for e in JS_EXTS]
        for c in cands:
            if c in paths:
                return c
    return None


def _tarjan_unused() -> None:  # placeholder to keep module surface explicit
    return None


def analyze(ctx: Ctx, deps: dict[str, Any], stack: dict[str, Any]) -> dict[str, Any]:
    reg = ctx.reg
    code = [f for f in ctx.files if f.language in CODE_LANGS and f.category in ("source", "test")
            and f.size <= config.MAX_READ_BYTES]
    paths = {f.path for f in ctx.files}
    py_files = [f.path for f in code if f.language == "Python"]
    index = PyIndex(py_files)
    java_map: dict[str, str] = {}
    java_pkg: dict[str, str] = {}
    for f in code:
        if f.language == "Java":
            for l in ctx.lines(f.path)[:60]:
                m = re.match(r"^\s*package\s+([\w.]+);", l)
                if m:
                    java_pkg[f.path] = m.group(1)
                    java_map[f"{m.group(1)}.{posixpath.splitext(posixpath.basename(f.path))[0]}"] = f.path
                    break

    edges: list[dict] = []
    routes: list[dict] = []
    models: list[dict] = []
    data_access: list[dict] = []
    entry_points: list[dict] = []
    api_calls: list[dict] = []
    parse_errors: list[str] = []
    tests = {f.path for f in code if f.category == "test"}

    def entry(kind, path, line, snippet, conf="high", nature="direct"):
        entry_points.append({"kind": kind, "path": path, "line": line, "snippet": snip(snippet),
                             "confidence": conf, "nature": nature})

    def add_edge(src, dst, line, method):
        if dst and dst != src:
            edges.append({"from": src, "to": dst, "line": line, "method": method})

    def db_hit(path, mod, line):
        for k, label in DB_LIBS.items():
            if mod == k or mod.startswith(k + ".") or mod.startswith(k + "/"):
                data_access.append({"path": path, "line": line, "library": label, "snippet": snip(mod)})
                return

    for f in code:
        lines = ctx.lines(f.path)
        if not lines:
            continue
        p = f.path
        base = posixpath.basename(p)
        text = "\n".join(lines)
        if f.language == "Python":
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    tree = ast.parse(text)
                es, ext = _py_edges(index, tree, p)
                for d, ln in es:
                    add_edge(p, d, ln, "python import")
                for mod, ln in ext:
                    db_hit(p, mod, ln)
            except (SyntaxError, ValueError, RecursionError, MemoryError):
                parse_errors.append(p)
            fw = "FastAPI" if "fastapi" in text else "Flask" if "flask" in text else None
            is_dj_urls = base == "urls.py"
            for i, l in enumerate(lines, 1):
                m = PY_ROUTE.match(l)
                if m:
                    routes.append({"method": m.group(2).upper(), "path": m.group(3), "file": p, "line": i,
                                   "framework": fw or "decorator-style", "snippet": snip(l)})
                    continue
                m = PY_FLASK.match(l)
                if m:
                    meths = re.findall(r"[A-Za-z]+", m.group(3) or "GET")
                    routes.append({"method": "/".join(x.upper() for x in meths), "path": m.group(2), "file": p,
                                   "line": i, "framework": fw or "Flask-style", "snippet": snip(l)})
                    continue
                if is_dj_urls:
                    m = DJ_PATH.search(l)
                    if m:
                        routes.append({"method": "ANY", "path": m.group(1), "file": p, "line": i,
                                       "framework": "Django", "snippet": snip(l), "handler": m.group(2)})
                if re.match(r"""^if\s+__name__\s*==\s*["']__main__["']""", l):
                    entry("python __main__ guard", p, i, l)
                m = re.search(r"^\s*(\w+)\s*=\s*(FastAPI|Flask)\(", l)
                if m:
                    entry(f"{m.group(2)} application object", p, i, l)
                m = PY_CLASS.match(l)
                if m:
                    cls, bases = m.group(1), m.group(2)
                    kind = None
                    if re.search(r"\bBaseModel\b|\bBaseSettings\b", bases):
                        kind = "pydantic-schema"
                    elif "models.Model" in bases:
                        kind = "django-model"
                    elif re.search(r"\bDeclarativeBase\b|\bSQLModel\b|\bdb\.Model\b|\bBase\b", bases) and \
                            re.search(r"sqlalchemy|sqlmodel|flask_sqlalchemy", text, re.I):
                        kind = "sqlalchemy-model"
                    if kind:
                        models.append({"name": cls, "kind": kind, "file": p, "line": i, "snippet": snip(l)})
            if base in ("manage.py", "wsgi.py", "asgi.py", "__main__.py"):
                entry(f"conventional entry file ({base})", p, 1, base, "medium", "inferred")
        elif f.language in ("JavaScript", "TypeScript", "Vue"):
            ctrl = None
            m = NEST_CTRL.search(text)
            if m:
                ctrl = m.group(1) or ""
            for i, l in enumerate(lines, 1):
                for m in JS_IMPORT.finditer(l):
                    spec = next(g for g in m.groups() if g)
                    r = _js_resolve(spec, p, paths)
                    if r:
                        add_edge(p, r, i, "js import")
                    elif not spec.startswith((".", "@/", "~/")):
                        db_hit(p, spec, i)
                if f.category == "source":
                    m = JS_ROUTE.search(l)
                    if m and m.group(2).startswith("/"):
                        routes.append({"method": m.group(1).upper(), "path": m.group(2), "file": p, "line": i,
                                       "framework": "Express-style", "snippet": snip(l)})
                    m = NEST_METHOD.match(l)
                    if m and ctrl is not None:
                        routes.append({"method": m.group(1).upper(), "path": _join(ctrl, m.group(2) or ""), "file": p,
                                       "line": i, "framework": "NestJS", "snippet": snip(l)})
                    m = JS_API_CALL.search(l)
                    if m and ("/api" in m.group(1) or "localhost" in m.group(1) or "127.0.0.1" in m.group(1)) \
                            and len(api_calls) < 200:
                        api_calls.append({"file": p, "line": i, "target": snip(m.group(1), 120), "snippet": snip(l)})
                    if re.search(r"(?:mongoose\.)?Schema\(", l) and "new" in l:
                        models.append({"name": re.sub(r"\W", "", l.split("=")[0].split()[-1]) if "=" in l else "(schema)",
                                       "kind": "mongoose-schema", "file": p, "line": i, "snippet": snip(l)})
                    m = re.search(r"\.define\(\s*[\"'](\w+)", l)
                    if m:
                        models.append({"name": m.group(1), "kind": "sequelize-model", "file": p, "line": i, "snippet": snip(l)})
                    if "@Entity(" in l:
                        for j in range(i, min(i + 5, len(lines))):
                            mc = re.search(r"class\s+(\w+)", lines[j])
                            if mc:
                                models.append({"name": mc.group(1), "kind": "typeorm-entity", "file": p, "line": i,
                                               "snippet": snip(l)})
                                break
            if base in ("index.js", "index.ts", "main.ts", "main.js", "main.tsx", "main.jsx", "server.js", "app.js", "index.tsx") \
                    and f.category == "source" and p.count("/") <= 2:
                entry(f"conventional entry file ({base})", p, 1, base, "medium", "inferred")
        elif f.language == "Java":
            pkg = java_pkg.get(p)
            class_line = next((i for i, l in enumerate(lines, 1) if re.search(r"\b(class|interface)\s+\w+", l)), None)
            prefix = ""
            for i, l in enumerate(lines, 1):
                m = re.match(r"^\s*import\s+(static\s+)?([\w.]+?)(\.\*)?;", l)
                if m:
                    fq = m.group(2)
                    hit = None
                    parts = fq.split(".")
                    for n in range(len(parts), 0, -1):
                        hit = java_map.get(".".join(parts[:n]))
                        if hit:
                            break
                    if hit:
                        add_edge(p, hit, i, "java import")
                    else:
                        db_hit(p, fq, i)
                mm = JAVA_MAP.search(l)
                if mm:
                    if class_line and i < class_line:
                        prefix = mm.group(2) or ""
                    elif class_line and f.category == "source":
                        verb = mm.group(1).upper() if mm.group(1) != "Request" else "ANY"
                        routes.append({"method": verb, "path": _join(prefix, mm.group(2) or ""), "file": p, "line": i,
                                       "framework": "Spring MVC", "snippet": snip(l)})
                if "@Entity" in l:
                    for j in range(i, min(i + 6, len(lines))):
                        mc = re.search(r"class\s+(\w+)", lines[j])
                        if mc:
                            models.append({"name": mc.group(1), "kind": "jpa-entity", "file": p, "line": i, "snippet": snip(l)})
                            break
                if "@SpringBootApplication" in l:
                    entry("Spring Boot application", p, i, l)
                elif re.search(r"public\s+static\s+void\s+main\s*\(", l):
                    entry("java main method", p, i, l)

    # non-code files: Prisma / SQL models, Dockerfile & package.json entry points
    for f in ctx.files:
        n = posixpath.basename(f.path).lower()
        if n.endswith(".prisma") or n.endswith(".sql"):
            for i, l in enumerate(ctx.lines(f.path), 1):
                if n.endswith(".prisma"):
                    m = re.match(r"^model\s+(\w+)\s*\{", l)
                    if m:
                        models.append({"name": m.group(1), "kind": "prisma-model", "file": f.path, "line": i, "snippet": snip(l)})
                else:
                    m = SQL_TABLE.search(l)
                    if m:
                        models.append({"name": m.group(1), "kind": "sql-table", "file": f.path, "line": i, "snippet": snip(l)})
        elif n.startswith("dockerfile"):
            for i, l in enumerate(ctx.lines(f.path), 1):
                if re.match(r"^\s*(CMD|ENTRYPOINT)\b", l, re.I):
                    entry("Dockerfile command", f.path, i, l)
        elif n == "package.json" and f.category == "config":
            try:
                import json
                data = json.loads(ctx.text(f.path))
                lines = ctx.lines(f.path)
                if isinstance(data, dict):
                    if data.get("main"):
                        entry("package.json main", f.path, next((i for i, l in enumerate(lines, 1) if '"main"' in l), None),
                              f'"main": "{data["main"]}"')
                    for k in ("start", "dev"):
                        v = (data.get("scripts") or {}).get(k)
                        if v:
                            entry(f"npm script '{k}'", f.path, next((i for i, l in enumerate(lines, 1) if f'"{k}"' in l), None),
                                  f'"{k}": "{v}"')
            except ValueError:
                pass
        elif n in ("main.go",) and f.language == "Go":
            for i, l in enumerate(ctx.lines(f.path), 1):
                if re.match(r"^func\s+main\s*\(", l):
                    entry("go main function", f.path, i, l)
        elif f.language == "Go" and f.category == "source":
            for i, l in enumerate(ctx.lines(f.path), 1):
                m = re.search(r"\.(GET|POST|PUT|DELETE|PATCH)\(\s*\"([^\"]+)\"", l)
                if m:
                    routes.append({"method": m.group(1), "path": m.group(2), "file": f.path, "line": i,
                                   "framework": "Go (gin/echo-style)", "snippet": snip(l)})
                m = re.search(r"HandleFunc\(\s*\"([^\"]+)\"", l)
                if m:
                    routes.append({"method": "ANY", "path": m.group(1), "file": f.path, "line": i,
                                   "framework": "net/http", "snippet": snip(l)})
        elif f.language == "PHP" and f.category == "source":
            for i, l in enumerate(ctx.lines(f.path), 1):
                m = re.search(r"Route::(get|post|put|delete|patch|any)\(\s*['\"]([^'\"]+)", l)
                if m:
                    routes.append({"method": m.group(1).upper(), "path": "/" + m.group(2).lstrip("/"), "file": f.path,
                                   "line": i, "framework": "Laravel", "snippet": snip(l)})
    for f in ctx.files:
        if f.language == "Go" and f.category == "source" and posixpath.basename(f.path) != "main.go":
            for i, l in enumerate(ctx.lines(f.path), 1):
                if re.match(r"^func\s+main\s*\(", l):
                    entry("go main function", f.path, i, l)
        if f.path.endswith(("public/index.php", "artisan")):
            entry("PHP entry file", f.path, 1, posixpath.basename(f.path), "medium", "inferred")

    edges = edges[:30000]

    # components (manifest directories)
    comp_map: dict[str, dict] = {}
    for m in deps["manifests"]:
        if m["kind"] != "manifest":
            continue
        d = posixpath.dirname(m["path"])
        c = comp_map.setdefault(d, {"path": d or ".", "name": posixpath.basename(d) or "(root)", "ecosystems": set(),
                                    "manifests": [], "frameworks": []})
        c["ecosystems"].add(m["ecosystem"])
        c["manifests"].append(m["path"])
    for fw in stack["frameworks"]:
        for loc in fw["locations"]:
            d = posixpath.dirname(loc["path"])
            if d in comp_map and fw["name"] not in comp_map[d]["frameworks"]:
                comp_map[d]["frameworks"].append(fw["name"])
    docker_dirs = {posixpath.dirname(f.path) for f in ctx.files if posixpath.basename(f.path).lower().startswith("dockerfile")}
    components = []
    for d, c in sorted(comp_map.items()):
        fr = set(c["frameworks"])
        role = "fullstack" if fr & FRONT and fr & BACK else "frontend" if fr & FRONT else "backend" if fr & BACK else "unknown"
        n_files = sum(1 for f in ctx.files if (not d or f.path.startswith(d + "/")) and f.category in ("source", "test"))
        comp = {"path": c["path"], "name": c["name"], "ecosystems": sorted(c["ecosystems"]), "manifests": c["manifests"],
                "frameworks": c["frameworks"], "role": role, "source_files": n_files, "has_dockerfile": d in docker_dirs}
        comp["finding_id"] = reg.add(
            "architecture", f"Component '{c['name']}' ({role}) at {c['path']}", c["manifests"][0],
            "manifest-directory heuristic", "medium", "inferred",
            {"frameworks": c["frameworks"], "manifests": c["manifests"], "role_basis": "framework dependencies"
             if role != "unknown" else "no recognised framework: role unresolved"})
        components.append(comp)

    # docker-compose services & relationships
    services, relationships = [], []
    for f in ctx.files:
        if posixpath.basename(f.path).lower().startswith(("docker-compose", "compose.")):
            for name, s in parse_compose(ctx.lines(f.path)).items():
                comp_path = None
                if s["build"]:
                    comp_path = posixpath.normpath(posixpath.join(posixpath.dirname(f.path), s["build"]))
                    comp_path = "" if comp_path == "." else comp_path
                services.append({"name": name, "image": s["image"], "build": s["build"], "ports": s["ports"],
                                 "depends_on": s["depends_on"], "path": f.path, "line": s["line"],
                                 "component": comp_path if comp_path in comp_map else None})
                ev = [{"path": f.path, "line": s["line"], "snippet": f"service {name}"}]
                if comp_path in comp_map:
                    relationships.append({"from": f"service:{name}", "to": f"component:{comp_map[comp_path]['path']}",
                                          "type": "builds", "nature": "inferred", "confidence": "medium", "evidence": ev})
                for dep in s["depends_on"]:
                    relationships.append({"from": f"service:{name}", "to": f"service:{dep}", "type": "depends_on",
                                          "nature": "direct", "confidence": "high", "evidence": ev})
    if services:
        reg.add("architecture", f"{len(services)} docker-compose services detected", services[0]["path"],
                "compose parser", "high", "direct",
                {"services": [{"name": s["name"], "image": s["image"], "build": s["build"]} for s in services[:12]]},
                lines=[services[0]["line"]])
    fronts = [c for c in components if c["role"] in ("frontend", "fullstack")]
    backs = [c for c in components if c["role"] in ("backend", "fullstack")]
    if len(fronts) == 1 and len(backs) == 1 and fronts[0] is not backs[0]:
        calls = [a for a in api_calls if a["file"].startswith(fronts[0]["path"] + "/") or fronts[0]["path"] == "."]
        if calls:
            relationships.append({"from": f"component:{fronts[0]['path']}", "to": f"component:{backs[0]['path']}",
                                  "type": "calls_api", "nature": "inferred", "confidence": "low",
                                  "evidence": [{"path": c["file"], "line": c["line"], "snippet": c["snippet"]} for c in calls[:3]]})
    for r in relationships[:50]:
        pass

    monorepo = len(components) > 1 or any(posixpath.basename(f.path) in ("pnpm-workspace.yaml", "lerna.json", "go.work") for f in ctx.files)
    if monorepo:
        reg.add("architecture", f"Multi-component / monorepo structure ({len(components)} manifest directories)",
                components[0]["manifests"][0] if components else None, "manifest-directory heuristic", "medium", "inferred",
                {"components": [c["path"] for c in components[:12]]})

    # route / model / data-access findings (grouped per file)
    for label, items, key in (("API routes", routes, "file"), ("Data models/schemas", models, "file"),
                              ("Database access", data_access, "path")):
        grouped: dict[str, list] = defaultdict(list)
        for it in items:
            grouped[it[key]].append(it)
        for path, its in list(grouped.items())[:60]:
            reg.add("architecture", f"{len(its)} {label.lower()} detected in file", path, "static pattern analysis",
                    "high" if label != "Database access" else "medium", "direct",
                    {"items": [{"line": i["line"], "snippet": i["snippet"]} for i in its[:6]]}, lines=[its[0]["line"]])
    for ep in entry_points[:60]:
        reg.add("structure", f"Entry point: {ep['kind']}", ep["path"], "pattern/convention", ep["confidence"], ep["nature"],
                {"snippet": ep["snippet"]}, lines=[ep["line"]] if ep["line"] else None)

    # tests -> source
    src_by_stem: dict[str, list[str]] = defaultdict(list)
    for f in code:
        if f.category == "source":
            src_by_stem[posixpath.splitext(posixpath.basename(f.path))[0].lower()].append(f.path)
    test_links, seen = [], set()
    for e in edges:
        if e["from"] in tests and e["to"] not in tests and (e["from"], e["to"]) not in seen:
            seen.add((e["from"], e["to"]))
            test_links.append({"test": e["from"], "source": e["to"], "method": "import resolution", "confidence": "medium"})
    for t in sorted(tests):
        stem = posixpath.splitext(posixpath.basename(t))[0]
        stem = re.sub(r"(?i)^test_|_test$|\.test$|\.spec$|tests?$", "", stem).lower()
        c = src_by_stem.get(stem, [])
        if stem and len(c) == 1 and (t, c[0]) not in seen:
            seen.add((t, c[0]))
            test_links.append({"test": t, "source": c[0], "method": "naming convention", "confidence": "low"})
    if test_links:
        reg.add("architecture", f"{len(test_links)} test-to-source relationships detected", test_links[0]["test"],
                "import resolution + naming convention", "medium", "inferred", {"sample": test_links[:5]})

    # module graph (directory level, non-test edges)
    def grp(p: str) -> str:
        parts = p.split("/")
        return "/".join(parts[:2]) if len(parts) > 2 else (parts[0] if len(parts) == 2 else "(root)")

    node_files: dict[str, int] = defaultdict(int)
    for f in code:
        if f.category == "source":
            node_files[grp(f.path)] += 1
    w: dict[tuple[str, str], int] = defaultdict(int)
    for e in edges:
        if e["from"] in tests or e["to"] in tests:
            continue
        a, b = grp(e["from"]), grp(e["to"])
        if a != b:
            w[(a, b)] += 1
    module_graph = {"nodes": [{"id": k, "files": v} for k, v in sorted(node_files.items(), key=lambda kv: -kv[1])[:40]],
                    "edges": [{"from": a, "to": b, "weight": n} for (a, b), n in sorted(w.items(), key=lambda kv: -kv[1])[:150]]}

    config_overview = [{"path": f.path, "category": f.category} for f in ctx.files
                       if f.category in ("config", "infra") and f.path.count("/") <= 2][:80]
    return {
        "components": components, "services": services, "relationships": relationships,
        "routes": routes[:2000], "models": models[:1000], "data_access": data_access[:500],
        "entry_points": entry_points[:100], "api_calls": api_calls, "import_edges": edges,
        "test_links": test_links[:2000], "module_graph": module_graph, "monorepo": monorepo,
        "configuration": config_overview, "parse_errors": parse_errors[:100],
        "graph_languages": ["Python", "JavaScript/TypeScript/Vue (relative & @/ imports)", "Java"],
    }
