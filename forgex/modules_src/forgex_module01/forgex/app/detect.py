"""Technology detection. Every item is backed by file/manifest evidence."""
from __future__ import annotations

import json
import posixpath
import re
from typing import Any

from .context import Ctx
from .dependencies import _norm
from .evidence import snip
from .infra import parse_compose

# (ecosystem, label, role, exact names, prefixes)
RULES = [
    ("pypi", "FastAPI", "backend", {"fastapi"}, ()),
    ("pypi", "Django", "backend", {"django"}, ()),
    ("pypi", "Flask", "backend", {"flask"}, ()),
    ("pypi", "SQLAlchemy", "orm", {"sqlalchemy", "flask-sqlalchemy"}, ()),
    ("pypi", "Pydantic", "library", {"pydantic"}, ()),
    ("pypi", "Celery", "tasks", {"celery"}, ()),
    ("pypi", "pytest", "testing", {"pytest"}, ()),
    ("npm", "React", "frontend", {"react"}, ()),
    ("npm", "Next.js", "frontend", {"next"}, ()),
    ("npm", "Vue", "frontend", {"vue"}, ()),
    ("npm", "Angular", "frontend", {"@angular/core"}, ()),
    ("npm", "Svelte", "frontend", {"svelte"}, ()),
    ("npm", "Express", "backend", {"express"}, ()),
    ("npm", "NestJS", "backend", {"@nestjs/core"}, ()),
    ("npm", "Vite", "build", {"vite"}, ()),
    ("npm", "Mongoose", "orm", {"mongoose"}, ()),
    ("npm", "Prisma", "orm", {"prisma", "@prisma/client"}, ()),
    ("npm", "TypeORM", "orm", {"typeorm"}, ()),
    ("npm", "Jest", "testing", {"jest"}, ()),
    ("maven", "Spring Boot", "backend", set(), ("spring-boot",)),
    ("maven", "Hibernate/JPA", "orm", {"hibernate-core"}, ("spring-boot-starter-data-jpa",)),
    ("maven", "JUnit", "testing", {"junit", "junit-jupiter"}, ()),
    ("composer", "Laravel", "backend", {"laravel/framework"}, ()),
    ("composer", "Symfony", "backend", {"symfony/framework-bundle"}, ()),
    ("go", "Gin", "backend", {"github.com/gin-gonic/gin"}, ()),
    ("go", "Echo", "backend", {"github.com/labstack/echo/v4"}, ()),
    ("go", "Fiber", "backend", {"github.com/gofiber/fiber/v2"}, ()),
]
DB_BY_DEP = {
    "pypi": {"psycopg2": "PostgreSQL", "psycopg2-binary": "PostgreSQL", "psycopg": "PostgreSQL", "asyncpg": "PostgreSQL",
             "pymysql": "MySQL", "mysqlclient": "MySQL", "mysql-connector-python": "MySQL", "aiomysql": "MySQL",
             "pymongo": "MongoDB", "motor": "MongoDB", "mongoengine": "MongoDB", "redis": "Redis", "aiosqlite": "SQLite"},
    "npm": {"pg": "PostgreSQL", "pg-promise": "PostgreSQL", "mysql": "MySQL", "mysql2": "MySQL", "mongodb": "MongoDB",
            "mongoose": "MongoDB", "redis": "Redis", "ioredis": "Redis", "sqlite3": "SQLite", "better-sqlite3": "SQLite"},
    "maven": {"postgresql": "PostgreSQL", "mysql-connector-java": "MySQL", "mysql-connector-j": "MySQL",
              "mongodb-driver-sync": "MongoDB", "spring-boot-starter-data-mongodb": "MongoDB", "h2": "H2",
              "sqlite-jdbc": "SQLite", "spring-boot-starter-data-redis": "Redis"},
    "go": {"github.com/lib/pq": "PostgreSQL", "github.com/go-sql-driver/mysql": "MySQL",
           "go.mongodb.org/mongo-driver": "MongoDB", "github.com/redis/go-redis": "Redis",
           "github.com/go-redis/redis": "Redis", "github.com/mattn/go-sqlite3": "SQLite"},
}
IMAGE_TECH = [("postgres", "PostgreSQL"), ("mysql", "MySQL"), ("mariadb", "MariaDB"), ("mongo", "MongoDB"),
              ("redis", "Redis"), ("elasticsearch", "Elasticsearch")]
CONN = re.compile(r"\b(postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|sqlite)(?:\+\w+)?://[^\s\"'<>]*")
CONN_TECH = {"postgres": "PostgreSQL", "postgresql": "PostgreSQL", "mysql": "MySQL", "mariadb": "MariaDB",
             "mongodb": "MongoDB", "mongodb+srv": "MongoDB", "redis": "Redis", "sqlite": "SQLite"}


def _key(d: dict) -> str:
    if d["ecosystem"] == "pypi":
        return _norm(d["name"])
    if d["ecosystem"] == "maven":
        return d["name"].split(":")[-1].lower()
    return d["name"].lower()


def detect(ctx: Ctx, deps: dict[str, Any]) -> dict[str, Any]:
    reg = ctx.reg
    stack: dict[str, list] = {k: [] for k in ("languages", "frameworks", "package_managers", "infrastructure", "databases")}

    # languages
    agg: dict[str, dict] = {}
    for f in ctx.files:
        if f.language and f.category in ("source", "test"):
            a = agg.setdefault(f.language, {"files": 0, "lines": 0, "paths": []})
            a["files"] += 1
            a["lines"] += f.lines
            if len(a["paths"]) < 3:
                a["paths"].append(f.path)
    total = sum(a["lines"] for a in agg.values()) or 1
    for name, a in sorted(agg.items(), key=lambda kv: -kv[1]["lines"]):
        fid = reg.add("technology", f"{name} source files present", a["paths"][0], "file extension", "high", "direct",
                      {"files": a["files"], "lines": a["lines"], "example_paths": a["paths"]})
        stack["languages"].append({"name": name, "files": a["files"], "lines": a["lines"],
                                   "percent": round(100 * a["lines"] / total, 1), "finding_id": fid})

    # frameworks (manifest evidence first, lockfile-only entries flagged medium)
    found: dict[str, dict] = {}
    for source, conf in (("manifest", "high"), ("lockfile", "medium")):
        for d in deps["dependencies"]:
            if d["source"] != source:
                continue
            k = _key(d)
            for eco, label, role, names, prefixes in RULES:
                if eco == d["ecosystem"] and (k in names or any(k.startswith(p) for p in prefixes)):
                    if source == "lockfile" and label in found:
                        continue
                    e = found.setdefault(label, {"role": role, "eco": eco, "conf": conf, "locs": []})
                    if len(e["locs"]) < 5:
                        e["locs"].append({"path": d["manifest"], "line": d["line"], "snippet": d["raw"]})
                    break
    for label, e in sorted(found.items()):
        first = e["locs"][0]
        fid = reg.add("technology", f"{label} detected ({e['role']})", first["path"],
                      "dependency declaration" if e["conf"] == "high" else "lockfile entry",
                      e["conf"], "direct", {"declarations": e["locs"]},
                      lines=[first["line"]] if first["line"] else None)
        stack["frameworks"].append({"name": label, "role": e["role"], "ecosystem": e["eco"], "confidence": e["conf"],
                                    "locations": e["locs"], "finding_id": fid})
    for m in deps["manifests"]:
        if m["type"] == "package.json" and m["parsed"]:
            data = {}
            try:
                data = json.loads(ctx.text(m["path"]))
            except ValueError:
                pass
            eng = (data.get("engines") or {}).get("node") if isinstance(data, dict) else None
            fid = reg.add("technology", "Node.js project (package.json present)", m["path"], "manifest file present",
                          "high" if eng else "medium", "direct",
                          {"engines.node": eng} if eng else {"note": "package.json present; runtime inferred"})
            if not any(f["name"] == "Node.js" for f in stack["frameworks"]):
                stack["frameworks"].append({"name": "Node.js", "role": "runtime", "ecosystem": "npm",
                                            "confidence": "high" if eng else "medium",
                                            "locations": [{"path": m["path"], "line": None, "snippet": "package.json"}],
                                            "finding_id": fid})

    # package managers
    pm_seen: dict[str, str] = {}
    for m in deps["manifests"]:
        t, p = m["type"], m["path"]
        name = {"requirements": "pip", "package.json": "npm", "package-lock.json": "npm", "yarn.lock": "Yarn",
                "pnpm-lock.yaml": "pnpm", "pom.xml": "Maven", "gradle": "Gradle", "go.mod": "Go modules",
                "composer.json": "Composer", "poetry.lock": "Poetry", "Pipfile.lock": "Pipenv", "uv.lock": "uv"}.get(t)
        if t == "pyproject":
            name = "Poetry" if "[tool.poetry]" in ctx.text(p) else ("uv" if "[tool.uv]" in ctx.text(p) else "pip (PEP 621)")
        if name and name not in pm_seen:
            pm_seen[name] = p
    for name, p in pm_seen.items():
        fid = reg.add("technology", f"Package manager: {name}", p, "manifest file", "high", "direct", {"file": p})
        stack["package_managers"].append({"name": name, "evidence_path": p, "finding_id": fid})

    # infrastructure
    infra: dict[str, list[str]] = {}
    yaml_checked = 0
    for f in ctx.files:
        n, low = posixpath.basename(f.path).lower(), f.path.lower()
        tech = None
        if n.startswith("dockerfile"):
            tech = "Docker"
        elif n.startswith(("docker-compose", "compose.")) and n.endswith((".yml", ".yaml")):
            tech = "Docker Compose"
        elif ".github/workflows/" in low and n.endswith((".yml", ".yaml")):
            tech = "GitHub Actions"
        elif n == ".gitlab-ci.yml":
            tech = "GitLab CI"
        elif n == "jenkinsfile":
            tech = "Jenkins"
        elif n.endswith(".tf"):
            tech = "Terraform"
        elif n == "azure-pipelines.yml":
            tech = "Azure Pipelines"
        elif ".circleci/" in low:
            tech = "CircleCI"
        elif n == "chart.yaml":
            tech = "Helm"
        elif n.endswith((".yml", ".yaml")) and f.size < 100_000 and yaml_checked < 300:
            yaml_checked += 1
            head = "\n".join(ctx.lines(f.path)[:60])
            if re.search(r"^apiVersion:", head, re.M) and re.search(r"^kind:", head, re.M):
                tech = "Kubernetes"
        if tech:
            infra.setdefault(tech, []).append(f.path)
    for tech, paths in sorted(infra.items()):
        fid = reg.add("technology", f"{tech} configuration present", paths[0], "infrastructure file", "high", "direct",
                      {"files": paths[:8], "count": len(paths)})
        stack["infrastructure"].append({"name": tech, "files": paths[:12], "finding_id": fid})

    # databases: evidence from drivers, compose images, connection strings
    dbs: dict[str, dict] = {}

    def add_db(tech, conf, ev):
        e = dbs.setdefault(tech, {"conf": conf, "evidence": []})
        if conf == "high":
            e["conf"] = "high"
        if len(e["evidence"]) < 6:
            e["evidence"].append(ev)

    for d in deps["dependencies"]:
        if d["source"] == "manifest":
            tech = DB_BY_DEP.get(d["ecosystem"], {}).get(_key(d))
            if tech is None and d["ecosystem"] == "go":
                tech = "PostgreSQL" if d["name"].startswith("github.com/jackc/pgx") else None
            if tech:
                add_db(tech, "medium", {"path": d["manifest"], "line": d["line"], "snippet": d["raw"],
                                        "kind": "client library declared (does not prove runtime use)"})
    for f in ctx.files:
        if posixpath.basename(f.path).lower().startswith(("docker-compose", "compose.")):
            for name, s in parse_compose(ctx.lines(f.path)).items():
                img = (s["image"] or "").lower()
                for key, tech in IMAGE_TECH:
                    if key in img:
                        add_db(tech, "high", {"path": f.path, "line": s["line"], "snippet": f"service {name}: image {s['image']}",
                                              "kind": "compose service image"})
        if f.category in ("config", "infra", "source") and f.size < 300_000:
            hits = 0
            for i, l in enumerate(ctx.lines(f.path), 1):
                m = CONN.search(l)
                if m and hits < 2:
                    hits += 1
                    red = re.sub(r"(://)[^@/\s]*@", r"\1[REDACTED]@", m.group(0))
                    add_db(CONN_TECH.get(m.group(1).split("+")[0], m.group(1)), "high",
                           {"path": f.path, "line": i, "snippet": snip(red), "kind": "connection string (credentials redacted)"})
    for tech, e in sorted(dbs.items()):
        ev = e["evidence"][0]
        fid = reg.add("technology", f"{tech} database technology indicated", ev["path"], "config/dependency evidence",
                      e["conf"], "direct", {"evidence": e["evidence"]}, lines=[ev["line"]] if ev.get("line") else None)
        stack["databases"].append({"name": tech, "confidence": e["conf"], "evidence": e["evidence"], "finding_id": fid})
    return stack
