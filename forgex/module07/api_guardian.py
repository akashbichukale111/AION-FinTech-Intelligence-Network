"""FORGEX Module 07 - API, Webhook & Payment Guardian.

Self-contained FastAPI router + services. Mount with:
    from api_guardian import router; app.include_router(router)
Read-only static analysis, deterministic in-process simulation (no network),
SQLite persistence, redacted audit trail. Cross-module hooks: see INTEGRATIONS.
"""
from __future__ import annotations

import ast
import hashlib
import hmac
import json
import os
import random
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- config
DB_PATH = os.environ.get("GUARDIAN_DB", "forgex_api_guardian.db")
ALLOWED_ROOTS = [Path(p).resolve() for p in os.environ.get("GUARDIAN_ALLOWED_ROOTS", os.getcwd()).split(os.pathsep) if p]
MAX_FILES, MAX_FILE_BYTES, MAX_SPEC_BYTES = 4000, 1_000_000, 5_000_000
SKIP_DIRS = {"node_modules", "venv", ".venv", "dist", "build", "migrations", "site-packages", "__pycache__",
             "tests", "test", "__tests__", "target", "vendor", "coverage"}
SRC_EXT = {".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".kt", ".json", ".yaml", ".yml", ".cfg", ".ini", ".toml"}

# Cross-module integration points. Modules 02/03/05/06 register callables here
# (e.g. INTEGRATIONS["module02.create_plan"] = planning_service.create_plan).
INTEGRATIONS: dict[str, Callable[..., Any]] = {}

SEV_W = {"critical": 40, "high": 25, "medium": 12, "low": 5, "info": 1}
CONF_W = {"high": 1.0, "medium": 0.7, "low": 0.4}
SEV_ORDER = ["info", "low", "medium", "high", "critical"]
HTTP = ("get", "post", "put", "delete", "patch", "head", "options")
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def uid(p: str) -> str:
    return f"{p}-{uuid.uuid4().hex[:10]}"


# --------------------------------------------------------------------------- redaction
SECRET_RX = [
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "high"),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "high"),
    ("stripe_live_key", re.compile(r"\b(?:sk|rk)_live_[0-9A-Za-z]{10,}"), "high"),
    ("github_token", re.compile(r"\bgh[pousr]_[0-9A-Za-z]{30,}"), "high"),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}"), "high"),
    ("hardcoded_secret", re.compile(
        r"""(?i)\b(api[_-]?key|secret(?:[_-]?key)?|access[_-]?token|auth[_-]?token|password|passwd|client[_-]?secret|webhook[_-]?secret|signing[_-]?secret)\b\s*[:=]\s*(['"])([^'"\s]{8,})\2"""),
     "medium"),
]
PLACEHOLDER = re.compile(r"(?i)(change.?me|example|your[_-]|xxx|<[^>]+>|\$\{|process\.env|os\.environ|dummy|placeholder|sample|\*{3,}|todo|here)")


def redact(text: str) -> str:
    if not text:
        return text
    for name, rx, _ in SECRET_RX:
        if name == "hardcoded_secret":
            text = rx.sub(lambda m: f"{m.group(1)}={m.group(2)}[REDACTED]{m.group(2)}", text)
        else:
            text = rx.sub("[REDACTED]", text)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}", r"\1[REDACTED]", text)
    return text


# --------------------------------------------------------------------------- persistence
_lock = threading.RLock()
_inited: set[str] = set()
SCHEMA = """
CREATE TABLE IF NOT EXISTS scans(id TEXT PRIMARY KEY, created_at TEXT, target TEXT, kind TEXT, status TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY, scan_id TEXT, type TEXT, data TEXT);
CREATE INDEX IF NOT EXISTS ix_items ON items(scan_id, type);
CREATE TABLE IF NOT EXISTS simulations(id TEXT PRIMARY KEY, created_at TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, finding_id TEXT, created_at TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, action TEXT, detail TEXT);
"""


def run(sql: str, args: tuple = ()) -> list[dict]:
    with _lock:
        c = sqlite3.connect(DB_PATH, check_same_thread=False)
        c.row_factory = sqlite3.Row
        try:
            if DB_PATH not in _inited:
                c.executescript(SCHEMA)
                _inited.add(DB_PATH)
            cur = c.execute(sql, args)
            c.commit()
            return [dict(r) for r in cur.fetchall()]
        finally:
            c.close()


def audit(action: str, **detail: Any) -> None:
    run("INSERT INTO audit(ts,action,detail) VALUES(?,?,?)", (now(), action, redact(json.dumps(detail, default=str))[:2000]))


def save_item(item_id: str, scan_id: str, typ: str, data: dict) -> None:
    run("INSERT OR REPLACE INTO items(id,scan_id,type,data) VALUES(?,?,?,?)", (item_id, scan_id, typ, json.dumps(data, default=str)))


def load_items(scan_id: str, typ: str) -> list[dict]:
    return [json.loads(r["data"]) for r in run("SELECT data FROM items WHERE scan_id=? AND type=? ORDER BY id", (scan_id, typ))]


def latest_scan_id() -> Optional[str]:
    r = run("SELECT id FROM scans WHERE status='completed' ORDER BY created_at DESC, rowid DESC LIMIT 1")
    return r[0]["id"] if r else None


# --------------------------------------------------------------------------- source loading
class Src:
    def __init__(self, rel: str, text: str):
        self.rel, self.text, self.lines = rel, text, text.splitlines()

    @property
    def ext(self) -> str:
        return Path(self.rel).suffix.lower()


def safe_root(p: str) -> Path:
    if re.match(r"^[a-z]+://", p, re.I):
        raise HTTPException(403, "External targets are not scanned. Provide a local repository path (read-only analysis).")
    root = Path(p).expanduser().resolve()
    if not root.is_dir():
        raise HTTPException(400, f"Not a directory: {p}")
    if not any(root == r or r in root.parents for r in ALLOWED_ROOTS):
        raise HTTPException(403, "Path is outside GUARDIAN_ALLOWED_ROOTS.")
    return root


def is_test_file(name: str) -> bool:
    return bool(re.match(r"(test_.*|.*_test\.py|.*\.(test|spec)\.[jt]sx?|conftest\.py)$", name))


def load_repo(root: Path) -> list[Src]:
    out: list[Src] = []
    for dp, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for n in names:
            p = Path(dp) / n
            if (p.suffix.lower() not in SRC_EXT and not n.startswith(".env")) or is_test_file(n) or p.is_symlink():
                continue
            try:
                if p.stat().st_size > MAX_FILE_BYTES:
                    continue
                out.append(Src(str(p.relative_to(root)).replace(os.sep, "/"), p.read_text(errors="replace")))
            except OSError:
                continue
            if len(out) >= MAX_FILES:
                return out
    return out


# --------------------------------------------------------------------------- helpers
AUTH_PY = re.compile(r"(auth|login_required|jwt|token|permission|current_user|require|secure|oauth|api_?key|bearer|verify_user|get_user|role)", re.I)
AUTH_JS = re.compile(r"\b(auth|authenticate|authorize|authMiddleware|requireAuth|isAuthenticated|ensureAuthenticated|verifyToken|jwtCheck|checkJwt|protect|requireLogin|isLoggedIn|requireRole|permit|guard|passport)\w*", re.I)
PUBLIC_ROUTE = re.compile(r"(login|logout|register|signup|sign-up|health|ping|status|docs|openapi|swagger|redoc|favicon|webhook|hook|callback|ipn|token|oauth|metrics|^/$)", re.I)


def norm(path: str) -> str:
    path = re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", path)
    path = re.sub(r"/:(\w+)", r"/{\1}", path)
    path = "/" + path.strip("/")
    return re.sub(r"/{2,}", "/", path)


def dotted(n: ast.AST) -> str:
    if isinstance(n, ast.Name):
        return n.id
    if isinstance(n, ast.Attribute):
        return dotted(n.value) + "." + n.attr
    if isinstance(n, ast.Call):
        return dotted(n.func)
    return ""


def const(n: Optional[ast.AST]) -> Optional[str]:
    return n.value if isinstance(n, ast.Constant) and isinstance(n.value, str) else None


def block(lines: list[str], i: int, maxn: int = 120) -> tuple[int, int]:
    depth, started, end = 0, False, i
    for j in range(i, min(len(lines), i + maxn)):
        for ch in lines[j]:
            if ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
        end = j
        if started and depth <= 0:
            break
    return i + 1, end + 1


Seg = tuple  # (file, start_line, text)


def find(segs: list[Seg], pattern: str, flags: int = re.I | re.M) -> Optional[tuple]:
    rx = re.compile(pattern, flags)
    for f, start, text in segs:
        m = rx.search(text)
        if m:
            line = start + text[:m.start()].count("\n")
            ln = text.splitlines()[text[:m.start()].count("\n")] if text else ""
            return (f, line, ln.strip()[:240])
    return None


def seg_lines(segs: list[Seg]) -> int:
    return sum(len(t.splitlines()) for _, _, t in segs)


class Ctx:
    def __init__(self, scan_id: str):
        self.scan_id = scan_id
        self.srcs: list[Src] = []
        self.models: dict[str, dict] = {}
        self.funcs: dict[str, list[Seg]] = {}
        self.jsdefs: dict[str, list[tuple]] = {}
        self.global_auth: Optional[str] = None
        self.spring_auth: Optional[str] = None
        self.drf_default: Optional[str] = None
        self.endpoints: list[dict] = []
        self.findings: list[dict] = []
        self.fps: set[str] = set()
        self.webhooks: list[dict] = []
        self.deps: list[dict] = []
        self.retries: list[dict] = []
        self.idem: list[dict] = []
        self.specs: list[dict] = []
        self.repo_text = ""

    def add(self, *, rule: str, category: str, severity: str, confidence: str, title: str, file: str, line: Optional[int],
            evidence: str, expected: str, actual: str, fix: str, tests: list[str], repro: str, ep: Optional[dict] = None,
            kind: str = "presence", sim: Optional[dict] = None, note: str = "") -> None:
        ref = f"{ep['method']} {ep['path']}" if ep else file
        fp = hashlib.sha1(f"{rule}|{ref}|{file}".encode()).hexdigest()[:16]
        if fp in self.fps:
            return
        self.fps.add(fp)
        needs_verification = False
        if kind == "absence":  # absence-of-evidence never exceeds 'medium' unless corroborated by caller
            needs_verification = True
            if confidence == "high" and not note.startswith("corroborated"):
                confidence = "medium"
        n = len(self.findings) + 1
        self.findings.append({
            "id": f"API-{self.scan_id[-6:].upper()}-{n:03d}", "scan_id": self.scan_id, "fingerprint": fp, "rule": rule,
            "title": title, "category": category, "severity": severity, "confidence": confidence,
            "endpoint": ref, "endpoint_id": ep["id"] if ep else None, "files": [file], "line": line,
            "evidence": {"kind": kind, "snippet": redact(evidence)[:700], "location": f"{file}:{line}" if line else file,
                         "note": note},
            "reproduction": repro, "expected": expected, "actual": actual, "actual_type": "static_analysis",
            "remediation": fix, "tests": tests, "needs_verification": needs_verification,
            "evidence_type": "static_analysis", "sim": sim,
        })


# --------------------------------------------------------------------------- pass 1: models, funcs, global signals
def pass1(ctx: Ctx) -> None:
    for s in ctx.srcs:
        ctx.repo_text += "\n" + s.text
        t = s.text
        if s.ext == ".py":
            try:
                tree = ast.parse(t)
            except (SyntaxError, ValueError):
                continue
            for n in ast.walk(tree):
                if isinstance(n, ast.ClassDef) and any(re.search(r"(BaseModel|Schema|Serializer)$", dotted(b)) for b in n.bases):
                    fields = {}
                    for st in n.body:
                        if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                            ann = ast.unparse(st.annotation)
                            val = ast.unparse(st.value) if st.value else ""
                            cons = bool(re.search(r"(max_length|min_length|ge=|le=|gt=|lt=|pattern|regex|max_items|max_digits)", val)
                                        or re.search(r"(conint|constr|condecimal|EmailStr|Literal|Enum|HttpUrl|UUID|Decimal|datetime|date\b|bool|conlist)", ann))
                            fields[st.target.id] = {"type": ann, "constrained": cons}
                    ctx.models[n.name] = {"fields": fields, "file": s.rel, "line": n.lineno}
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    start = min([d.lineno for d in n.decorator_list] + [n.lineno])
                    ctx.funcs.setdefault(n.name, []).append((s.rel, start, "\n".join(s.lines[start - 1:n.end_lineno])))
            if re.search(r"before_request|add_middleware\(.*(Auth|JWT|Session)|AuthenticationMiddleware|include_router\([^)]*dependencies", t, re.S):
                ctx.global_auth = ctx.global_auth or f"global auth hook in {s.rel}"
            m = re.search(r"DEFAULT_PERMISSION_CLASSES['\"]?\s*:\s*[\[(]([^\]\)]*)", t)
            if m:
                ctx.drf_default = m.group(1)
        elif s.ext in (".js", ".ts", ".jsx", ".tsx"):
            for i, l in enumerate(s.lines):
                m = re.search(r"(?:function\s+(\w+)|\b(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:function|\([^)]*\)\s*=>|\w+\s*=>)|(?:exports|module\.exports)\.(\w+)\s*=|^\s*(?:async\s+)?(\w+)\s*\([^)]*\)\s*\{)", l)
                if m:
                    name = next(g for g in m.groups() if g)
                    if name not in {"if", "for", "while", "switch", "catch", "function", "return"}:
                        ctx.jsdefs.setdefault(name, []).append((s, i))
            if re.search(r"\bapp\.use\(\s*(?:[\w.]*)(auth|jwt|passport|authenticate)\w*", t, re.I):
                ctx.global_auth = ctx.global_auth or f"app.use(auth middleware) in {s.rel}"
        elif s.ext in (".java", ".kt"):
            if re.search(r"anyRequest\(\)\s*\.\s*authenticated\(\)", t):
                ctx.spring_auth = f"Spring Security anyRequest().authenticated() in {s.rel}"
            if re.search(r"anyRequest\(\)\s*\.\s*permitAll\(\)", t):
                ctx.spring_auth = "PERMIT_ALL"


# --------------------------------------------------------------------------- endpoint discovery
def finalize_ep(ctx: Ctx, ep: dict, segs: list[Seg]) -> None:
    ep["_segs"] = segs
    ctx.endpoints.append(ep)


def mk_ep(src_rel: str, fw: str, method: str, path: str, handler: str, start: int, end: int, auth: tuple,
          params: Optional[list] = None, validation: str = "n/a", response: Optional[str] = None, source: str = "code") -> dict:
    return {"method": method, "path": norm(path), "framework": fw, "file": src_rel, "line": start, "end_line": end,
            "handler": handler, "auth": auth[0], "auth_evidence": auth[1], "auth_scope": auth[2],
            "params": params or [], "validation": validation, "response_model": response, "source": source}


def py_endpoints(src: Src, ctx: Ctx) -> None:
    try:
        tree = ast.parse(src.text)
    except (SyntaxError, ValueError):
        return
    routers: dict[str, tuple[str, bool]] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and dotted(n.value.func).split(".")[-1] in ("APIRouter", "FastAPI", "Blueprint", "Flask"):
            kw = {k.arg: k.value for k in n.value.keywords}
            prefix = const(kw.get("prefix")) or const(kw.get("url_prefix")) or ""
            dep = "dependencies" in kw and bool(AUTH_PY.search(ast.unparse(kw["dependencies"])))
            for t in n.targets:
                if isinstance(t, ast.Name):
                    routers[t.id] = (prefix, dep)
    fw = "fastapi" if "fastapi" in src.text.lower() else "flask"
    flask_validation = bool(re.search(r"(marshmallow|pydantic|cerberus|jsonschema|wtforms|validate\()", src.text))
    for n in ast.walk(tree):
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for d in n.decorator_list:
            if not (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)):
                continue
            attr = d.func.attr.lower()
            kw = {k.arg: k.value for k in d.keywords}
            if attr in HTTP:
                methods = [attr.upper()]
            elif attr == "route":
                m = kw.get("methods")
                methods = [x.value.upper() for x in m.elts if isinstance(x, ast.Constant)] if isinstance(m, (ast.List, ast.Tuple)) else ["GET"]
            else:
                continue
            path = const(d.args[0]) if d.args else const(kw.get("path"))
            if path is None:
                continue
            prefix, rdep = routers.get(dotted(d.func.value), ("", False))
            start = min(x.lineno for x in n.decorator_list)
            text = "\n".join(src.lines[start - 1:n.end_lineno])
            auth_ev = next((ast.unparse(x) for x in n.decorator_list if x is not d and AUTH_PY.search(ast.unparse(x))), None)
            if "dependencies" in kw and AUTH_PY.search(ast.unparse(kw["dependencies"])):
                auth_ev = "dependencies=" + ast.unparse(kw["dependencies"])
            a = n.args
            pos = a.posonlyargs + a.args
            defaults = [None] * (len(pos) - len(a.defaults)) + list(a.defaults)
            allargs = list(zip(pos, defaults)) + list(zip(a.kwonlyargs, a.kw_defaults))
            params, body_model, raw_body = [], None, False
            path_params = set(re.findall(r"\{(\w+)\}|<(?:\w+:)?(\w+)>", path) and [x for t in re.findall(r"\{(\w+)\}|<(?:\w+:)?(\w+)>", path) for x in t if x])
            for arg, df in allargs:
                if arg.arg in ("self", "request", "req", "db", "session"):
                    if arg.arg in ("request", "req"):
                        raw_body = True
                    continue
                dtxt = ast.unparse(df) if df is not None else ""
                if "Depends(" in dtxt or "Security(" in dtxt:
                    if AUTH_PY.search(dtxt):
                        auth_ev = auth_ev or dtxt
                    continue
                ann = ast.unparse(arg.annotation) if arg.annotation else ""
                base = re.sub(r"[\[\]].*", "", ann)
                if base in ctx.models:
                    body_model = base
                    params.append({"name": arg.arg, "in": "body", "type": base, "required": df is None})
                elif ann in ("dict", "Dict", "Any") or "dict[" in ann.lower():
                    raw_body = True
                else:
                    params.append({"name": arg.arg, "in": "path" if arg.arg in path_params else "query", "type": ann or "any", "required": df is None})
            body_txt = "\n".join(src.lines[n.lineno - 1:n.end_lineno])
            reads_json = bool(re.search(r"(get_json\(|request\.json|await\s+request\.json|request\.form|request\.data)", body_txt))
            mutating = any(m in MUTATING for m in methods)
            if body_model:
                validation = "schema"
            elif mutating and (raw_body or reads_json):
                validation = "schema" if (fw == "flask" and flask_validation and reads_json) else "none"
            else:
                validation = "n/a" if not mutating else ("schema" if params else "none")
            if auth_ev:
                auth = ("present", auth_ev, "direct")
            elif rdep:
                auth = ("present", "router-level dependencies", "inherited")
            elif ctx.global_auth:
                auth = ("unknown", ctx.global_auth, "global")
            else:
                auth = ("absent", "no auth decorator/dependency found", "none")
            resp = const(kw.get("response_model")) or (dotted(kw["response_model"]) if "response_model" in kw else None)
            if not resp and n.returns is not None:
                resp = ast.unparse(n.returns)
            for m in methods:
                ep = mk_ep(src.rel, fw, m, prefix + path, n.name, start, n.end_lineno, auth, list(params), validation, resp)
                ep["body_model"] = body_model
                segs = [(src.rel, start, text)]
                seen = {n.name}
                for callee in re.findall(r"\b(\w+)\(", body_txt):
                    if callee in ctx.funcs and callee not in seen and len(segs) < 7:
                        seen.add(callee)
                        segs.extend(ctx.funcs[callee][:2])
                finalize_ep(ctx, ep, segs)


EXP_RE = re.compile(r"""\b(\w+)\.(get|post|put|patch|delete)\(\s*(['"`])([^'"`]+)\3\s*([,)])""")
NOT_RECV = {"req", "res", "map", "cache", "config", "headers", "params", "axios", "http", "https", "client", "session", "redis",
            "localStorage", "process", "searchParams", "this", "url", "form", "formData", "Reflect", "api", "request", "supertest"}


def js_endpoints(src: Src, ctx: Ctx) -> None:
    if not re.search(r"express|Router\(|fastify", src.text):
        return
    router_use = [(m.group(1), i) for i, l in enumerate(src.lines) for m in [re.search(r"\b(\w+)\.use\((.*)\)", l)]
                  if m and AUTH_JS.search(m.group(2))]
    for i, l in enumerate(src.lines):
        m = EXP_RE.search(l)
        if not m or m.group(1) in NOT_RECV:
            continue
        recv, meth, _, path, _ = m.groups()
        s, e = block(src.lines, i)
        after = "\n".join([l[m.end():]] + src.lines[i + 1:i + 3])
        cut = re.split(r"=>|function\b|\(\s*\{?\s*req\b", after)[0]
        items = [x.strip() for x in cut.split(",")]
        mw = items[:-1] if len(items) > 1 else []
        auth_ev = next((x for x in mw if AUTH_JS.search(x)), None)
        segs = [(src.rel, s, "\n".join(src.lines[s - 1:e]))]
        hname = items[-1].strip(");{ ") if items else ""
        if re.fullmatch(r"[\w.]+", hname or "-"):
            last = hname.split(".")[-1]
            for hs, hi in ctx.jsdefs.get(last, [])[:1]:
                a, b = block(hs.lines, hi)
                segs.append((hs.rel, a, "\n".join(hs.lines[a - 1:b])))
        if auth_ev:
            auth = ("present", auth_ev, "direct")
        elif any(r == recv and ln < i for r, ln in router_use):
            auth = ("present", f"{recv}.use(auth middleware)", "inherited")
        elif ctx.global_auth:
            auth = ("unknown", ctx.global_auth, "global")
        else:
            auth = ("absent", "no auth middleware in route chain", "none")
        txt = "\n".join(t for _, _, t in segs)
        mutating = meth.upper() in MUTATING
        validation = "n/a"
        if mutating:
            validation = "schema" if re.search(r"(joi|zod|ajv|express-validator|celebrate|yup|validate\w*\(|schema\.parse|body\()", txt + "\n".join(src.lines[max(0, i - 2):i + 1]), re.I) else \
                ("none" if re.search(r"req\.body", txt) else "n/a")
        ep = mk_ep(src.rel, "express", meth.upper(), path, hname or "<inline>", i + 1, e, auth, [], validation)
        finalize_ep(ctx, ep, segs)


SPRING_RE = re.compile(r"@(Get|Post|Put|Delete|Patch|Request)Mapping\s*(\(([^)]*)\))?")


def spring_endpoints(src: Src, ctx: Ctx) -> None:
    if "Mapping" not in src.text:
        return
    L = src.lines
    base, class_auth = "", None
    for i, l in enumerate(L):
        m = SPRING_RE.search(l)
        if not m:
            continue
        kind, args = m.group(1), m.group(3) or ""
        window_next = "\n".join(L[i + 1:i + 4])
        pm = re.search(r'"([^"]*)"', args)
        if kind == "Request" and re.search(r"\bclass\b", window_next):
            base = pm.group(1) if pm else ""
            pre = "\n".join(L[max(0, i - 4):i + 4])
            am = re.search(r"@(PreAuthorize|Secured|RolesAllowed)\([^)]*\)", pre)
            class_auth = am.group(0) if am else None
            continue
        if kind == "Request":
            mm = re.search(r"RequestMethod\.(\w+)", args)
            method = mm.group(1) if mm else "GET"
        else:
            method = kind.upper()
        sig = "\n".join(L[i:i + 8])
        name = re.search(r"\b(\w+)\s*\(", re.sub(r"@\w+(\([^)]*\))?", "", "\n".join(L[i + 1:i + 5])))
        pre = "\n".join(L[max(0, i - 5):i + 1])
        am = re.search(r"@(PreAuthorize|Secured|RolesAllowed)\([^)]*\)", pre)
        auth_ev = am.group(0) if am else class_auth
        if auth_ev:
            auth = ("present", auth_ev, "direct")
        elif ctx.spring_auth and ctx.spring_auth != "PERMIT_ALL":
            auth = ("present", ctx.spring_auth, "inherited")
        elif ctx.spring_auth == "PERMIT_ALL":
            auth = ("absent", "anyRequest().permitAll()", "none")
        else:
            auth = ("unknown", "no Spring Security config found in scanned files", "none")
        validation = "n/a"
        if method in MUTATING:
            validation = "schema" if re.search(r"@(Valid|Validated)\b", sig) else ("partial" if "@RequestBody" in sig else "none")
        s, e = block(L, i)
        ep = mk_ep(src.rel, "spring", method, base + "/" + (pm.group(1) if pm else ""), name.group(1) if name else "?", i + 1, e, auth, [], validation)
        finalize_ep(ctx, ep, [(src.rel, s, "\n".join(L[s - 1:e]))])


def drf_endpoints(src: Src, ctx: Ctx) -> None:
    if "rest_framework" not in src.text:
        return
    L, t = src.lines, src.text
    routes: dict[str, str] = {}
    for u in ctx.srcs:
        if u.rel.endswith("urls.py"):
            for m in re.finditer(r"path\(\s*['\"]([^'\"]*)['\"]\s*,\s*([\w.]+?)(?:\.as_view\([^)]*\))?\s*[,)]", u.text):
                routes[m.group(2).split(".")[-1]] = m.group(1)
            for m in re.finditer(r"register\(\s*r?['\"]([^'\"]*)['\"]\s*,\s*(\w+)", u.text):
                routes[m.group(2)] = m.group(1)

    def auth_of(txt: str):
        pc = re.search(r"permission_classes\s*=\s*[\[(]([^\])]*)", txt) or re.search(r"@permission_classes\(\s*[\[(]([^\])]*)", txt)
        if pc:
            v = pc.group(1).strip()
            if not v or "AllowAny" in v:
                return ("absent", f"permission_classes={v or '[]'}", "explicit")
            return ("present", f"permission_classes={v}", "direct")
        if ctx.drf_default and "AllowAny" not in ctx.drf_default:
            return ("present", f"DEFAULT_PERMISSION_CLASSES={ctx.drf_default.strip()}", "inherited")
        return ("absent", "no permission_classes; DRF default is AllowAny", "default")

    for m in re.finditer(r"^class\s+(\w+)\(([^)]*)\):", t, re.M):
        name, bases = m.group(1), m.group(2)
        if not re.search(r"(APIView|ViewSet|GenericAPIView|ListAPIView|CreateAPIView|ModelViewSet|Mixin)", bases):
            continue
        i = t[:m.start()].count("\n")
        s, e = block_py(L, i)
        body = "\n".join(L[s - 1:e])
        a = auth_of(body)
        route = routes.get(name, f"(unresolved)/{name}")
        meths = [x.upper() for x in re.findall(r"^\s+def\s+(get|post|put|patch|delete)\(", body, re.M)]
        if "ModelViewSet" in bases:
            meths = ["GET", "POST", "PUT", "PATCH", "DELETE"]
        for me in meths:
            valid = "schema" if re.search(r"(serializer_class|Serializer\()", body) else ("none" if me in MUTATING and "request.data" in body else "n/a")
            finalize_ep(ctx, mk_ep(src.rel, "drf", me, route, name, s, e, a, [], valid), [(src.rel, s, body)])
    for m in re.finditer(r"@api_view\(\s*\[([^\]]*)\]\s*\)\s*\n((?:\s*@[^\n]+\n)*)\s*def\s+(\w+)", t):
        i = t[:m.start()].count("\n")
        s, e = block_py(L, i + 1 + m.group(2).count("\n") + 1)
        body = "\n".join(L[i:e])
        for me in re.findall(r"['\"](\w+)['\"]", m.group(1)):
            valid = "schema" if "Serializer(" in body else ("none" if me.upper() in MUTATING and "request.data" in body else "n/a")
            finalize_ep(ctx, mk_ep(src.rel, "drf", me.upper(), routes.get(m.group(3), f"(unresolved)/{m.group(3)}"), m.group(3), i + 1, e, auth_of(body), [], valid), [(src.rel, i + 1, body)])


def block_py(lines: list[str], i: int) -> tuple[int, int]:
    indent = len(lines[i]) - len(lines[i].lstrip()) if i < len(lines) else 0
    end = i
    for j in range(i + 1, len(lines)):
        if lines[j].strip() and len(lines[j]) - len(lines[j].lstrip()) <= indent and not lines[j].lstrip().startswith(("@", ")")):
            break
        end = j
    return i + 1, end + 1


# --------------------------------------------------------------------------- OpenAPI analyzer
def load_spec(text: str) -> dict:
    if len(text) > MAX_SPEC_BYTES:
        raise HTTPException(413, "OpenAPI document too large")
    try:
        return json.loads(text)
    except ValueError:
        try:
            import yaml  # optional
            return yaml.safe_load(text)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"Unparseable OpenAPI document (JSON/YAML): {e}")


def deref(spec: dict, node: Any, depth: int = 0) -> Any:
    if isinstance(node, dict) and "$ref" in node and isinstance(node["$ref"], str) and node["$ref"].startswith("#/") and depth < 6:
        cur: Any = spec
        for p in node["$ref"][2:].split("/"):
            cur = cur.get(p, {}) if isinstance(cur, dict) else {}
        return deref(spec, cur, depth + 1)
    return node


def schema_stats(spec: dict, sc: Any, depth: int = 0) -> dict:
    sc = deref(spec, sc)
    out = {"fields": 0, "constrained": 0, "unbounded_strings": [], "required": [], "props": {}}
    if not isinstance(sc, dict) or depth > 3:
        return out
    props = sc.get("properties", {}) or {}
    out["required"] = list(sc.get("required", []) or [])
    for k, v in props.items():
        v = deref(spec, v)
        out["fields"] += 1
        typ = v.get("type") if isinstance(v, dict) else None
        out["props"][k] = typ
        cons = isinstance(v, dict) and any(x in v for x in ("maxLength", "pattern", "enum", "maximum", "minimum", "format", "maxItems"))
        out["constrained"] += int(cons)
        if typ == "string" and not cons:
            out["unbounded_strings"].append(k)
    return out


def analyze_openapi(ctx: Ctx, text: str, name: str) -> None:
    spec = load_spec(text)
    if not isinstance(spec, dict) or not ("openapi" in spec or "swagger" in spec):
        raise HTTPException(400, "Document is not an OpenAPI/Swagger specification")
    gsec = spec.get("security")
    schemes = (spec.get("components", {}) or {}).get("securitySchemes", {}) or spec.get("securityDefinitions", {}) or {}
    servers = [s.get("url", "") for s in spec.get("servers", []) or []]
    ctx.specs.append({"name": name, "version": spec.get("openapi") or spec.get("swagger"), "title": (spec.get("info") or {}).get("title"),
                      "servers": servers, "paths": len(spec.get("paths", {}) or {})})
    for srv in servers:
        if srv.startswith("http://") and not re.search(r"localhost|127\.0\.0\.1", srv):
            ctx.add(rule="openapi.insecure_server", category="security", severity="medium", confidence="high",
                    title="OpenAPI declares a plaintext HTTP server", file=name, line=None, evidence=f"servers: {srv}",
                    expected="Production servers use https://", actual=f"Spec advertises {srv}",
                    fix="Declare https:// servers and enforce TLS at the edge.", tests=["Contract test asserting all servers[].url use https"],
                    repro="Read servers[] in the specification.")
    for sn, sv in schemes.items():
        if isinstance(sv, dict) and sv.get("type") == "apiKey" and sv.get("in") == "query":
            ctx.add(rule="openapi.apikey_in_query", category="security", severity="medium", confidence="high",
                    title=f"API key '{sn}' is passed in the query string", file=name, line=None, evidence=f"securitySchemes.{sn}: apiKey in query",
                    expected="Credentials are sent in headers", actual="Query-string credentials land in logs, proxies and referrers",
                    fix="Move the key to an Authorization or X-API-Key header.", tests=["Assert no securityScheme uses in: query"],
                    repro="Inspect components.securitySchemes.")
        if isinstance(sv, dict) and sv.get("type") == "http" and sv.get("scheme") == "basic":
            ctx.add(rule="openapi.basic_auth", category="security", severity="low", confidence="high",
                    title=f"HTTP Basic authentication declared ({sn})", file=name, line=None, evidence=f"securitySchemes.{sn}: http/basic",
                    expected="Token-based auth (OAuth2/JWT) with scoped credentials", actual="Static credentials sent on every call",
                    fix="Prefer OAuth2/OIDC bearer tokens with expiry and scopes.", tests=["Security scheme policy test"], repro="Inspect components.securitySchemes.")
    any429 = False
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        shared = item.get("parameters", [])
        for method, op in item.items():
            if method.lower() not in HTTP or not isinstance(op, dict):
                continue
            M = method.upper()
            sec = op.get("security", gsec)
            if sec is None:
                auth = ("absent", "no global or operation security", "none")
            elif sec == [] or sec == [{}]:
                auth = ("absent", "security: [] (explicitly public)", "explicit")
            else:
                auth = ("present", json.dumps(sec)[:120], "direct")
            params = [deref(spec, p) for p in (shared + op.get("parameters", []))]
            pinfo = [{"name": p.get("name"), "in": p.get("in"), "required": bool(p.get("required")), "type": (deref(spec, p.get("schema", {})) or {}).get("type")} for p in params if isinstance(p, dict)]
            rb = deref(spec, op.get("requestBody", {}))
            rbs = {}
            if isinstance(rb, dict):
                for ct, v in (rb.get("content") or {}).items():
                    rbs = schema_stats(spec, (v or {}).get("schema", {}))
                    break
            resp = op.get("responses", {}) or {}
            codes = sorted(str(c) for c in resp)
            any429 = any429 or "429" in codes
            validation = "n/a" if M not in MUTATING else ("schema" if rbs.get("fields") else "none")
            ep = mk_ep(name, "openapi", M, path, op.get("operationId", ""), 0, 0, auth, pinfo, validation, ",".join(codes), source="openapi")
            ep["line"] = None
            ep["contract"] = {"required_params": sorted(p["name"] for p in pinfo if p["required"] and p["name"]),
                              "required_body": rbs.get("required", []), "response_codes": codes, "body_props": rbs.get("props", {})}
            ep["_segs"] = []
            ctx.endpoints.append(ep)
            if M in MUTATING and auth[0] == "absent" and not PUBLIC_ROUTE.search(path):
                ctx.add(rule="openapi.unsecured_mutation", category="security", severity="medium", confidence="medium", ep=ep,
                        title=f"{M} {path} declares no security requirement", file=name, line=None,
                        evidence=f"{auth[1]}", expected="State-changing operations declare an authentication scheme",
                        actual="Specification marks the operation as unauthenticated (gateway enforcement may exist but is undocumented)",
                        fix="Add a security requirement or document the compensating control.", tests=["Contract test: every mutating operation has security"],
                        repro="Read the operation's security / global security.", kind="absence")
            if auth[0] == "present" and not any(c in codes for c in ("401", "403")):
                ctx.add(rule="openapi.no_auth_errors", category="error_handling", severity="low", confidence="high", ep=ep,
                        title=f"{M} {path} is secured but documents no 401/403", file=name, line=None, evidence=f"responses: {codes}",
                        expected="401/403 documented for secured operations", actual="Clients cannot rely on a documented auth-failure contract",
                        fix="Document 401/403 with a standard error schema.", tests=["Contract test for documented auth errors"], repro="Read responses.")
            if M in MUTATING and re.search(r"(pay|charge|refund|capture|order|transfer)", path, re.I) and \
                    not any((p.get("name") or "").lower() in ("idempotency-key", "x-idempotency-key") for p in params if isinstance(p, dict)):
                ctx.add(rule="openapi.payment_no_idempotency_key", category="payment", severity="medium", confidence="medium", ep=ep,
                        title=f"{M} {path} documents no Idempotency-Key header", file=name, line=None, evidence=f"parameters: {[p['name'] for p in pinfo]}",
                        expected="Payment mutations accept an Idempotency-Key", actual="Clients have no contract-level way to retry safely",
                        fix="Add an Idempotency-Key header parameter and document replay semantics (same key + same body => same response).",
                        tests=["Duplicate request with same key returns original result"], repro="Read operation parameters.", kind="absence")
            if rbs.get("unbounded_strings"):
                ctx.add(rule="openapi.unbounded_strings", category="validation", severity="low", confidence="medium", ep=ep,
                        title=f"{M} {path} request body has unconstrained strings", file=name, line=None,
                        evidence=f"no maxLength/pattern/enum/format: {rbs['unbounded_strings'][:6]}", expected="String inputs are bounded",
                        actual="Unbounded inputs permit oversized payloads and weak validation", fix="Add maxLength/pattern/format to string properties.",
                        tests=["Boundary tests for oversized strings"], repro="Read request schema.", kind="absence")
            for rcode, rv in resp.items():
                rs = schema_stats(spec, (((deref(spec, rv) or {}).get("content") or {}).get("application/json") or {}).get("schema", {}))
                bad = [k for k in rs["props"] if re.search(r"(password|secret|ssn|cvv|card_?number|api_?key|token)", k, re.I)]
                if bad:
                    ctx.add(rule="openapi.sensitive_response_field", category="security", severity="medium", confidence="medium", ep=ep,
                            title=f"{M} {path} response schema exposes sensitive field(s)", file=name, line=None, evidence=f"{rcode} response properties: {bad}",
                            expected="Responses exclude secrets and credentials", actual="Schema permits sensitive data in responses",
                            fix="Remove or mask the fields; use write-only properties for secrets.", tests=["Response schema must not contain sensitive property names"], repro="Read response schema.")
        for cbk in [c for o in item.values() if isinstance(o, dict) for c in (o.get("callbacks") or {})]:
            ctx.webhooks.append({"id": f"WH-OA-{len(ctx.webhooks) + 1:02d}", "ref": f"callback {cbk} ({path})", "file": name, "line": None,
                                 "source": "openapi", "checks": {}, "status": "documented"})
    for wn, wv in (spec.get("webhooks") or {}).items():
        sigdoc = json.dumps(wv).lower()
        ctx.webhooks.append({"id": f"WH-OA-{len(ctx.webhooks) + 1:02d}", "ref": f"webhook {wn}", "file": name, "line": None, "source": "openapi",
                             "checks": {"signature": "present" if "signature" in sigdoc else "absent"}, "status": "documented"})
    if not any429 and spec.get("paths"):
        ctx.add(rule="openapi.no_429", category="rate_limit", severity="low", confidence="low",
                title="No operation documents HTTP 429", file=name, line=None, evidence="no 429 response in any operation",
                expected="Rate-limited APIs document 429 and Retry-After", actual="Rate limit contract is undocumented (limits may exist at a gateway)",
                fix="Document 429 + Retry-After and the limiter policy.", tests=["Contract test for 429 shape"], repro="Search responses for 429.", kind="absence")


# --------------------------------------------------------------------------- feature extraction
RX = {
    "verify": r"(compare_digest|construct_?event|verify_?signature|validate_?signature|verify_?webhook|validate_?webhook|verifyHeader|createHmac|timingSafeEqual|hmac\.new|hmac\.HMAC|Mac\.getInstance|MessageDigest\.isEqual|signature_valid|is_valid_signature|validate_webhook_signature|verify_webhook_signature)",
    "const_time": r"(compare_digest|timingSafeEqual|MessageDigest\.isEqual|construct_?event|verifyHeader|validate_webhook_signature)",
    "timestamp": r"(tolerance|X-[\w-]*Timestamp|abs\(\s*(time|now)|time\.time\(\)\s*-|Date\.now\(\)\s*-|\.getTime\(\)|timestamp\s*[<>]|replay|construct_?event|webhook_?timestamp)",
    "dedupe": r"(processed_?event|already_?(processed|handled)|event_?id|eventId|event\[['\"]id|idempoten|get_or_create|insert or ignore|on conflict|setnx|nx\s*=\s*true|\.exists\()",
    "atomic": r"(unique|on conflict|insert or ignore|get_or_create|setnx|nx\s*=\s*true|IntegrityError|DuplicateKey|UniqueConstraint|\.add\(\s*[^)]*\)\s*==|atomic)",
    "async": r"(\.delay\(|apply_async|enqueue|\.send_task|BackgroundTasks|background_tasks|add_task|queue\.|\.publish\(|kafka|sqs|bull|setImmediate|@Async|executor\.submit|publishEvent)",
    "heavy": r"(requests\.(get|post|put)|httpx\.|urllib\.request|time\.sleep|send_mail|smtplib|axios\.|\bfetch\(|RestTemplate|WebClient|sendEmail|subprocess)",
    "persist": r"(WebhookEvent|webhook_events?|events?\.(insert|create|add)|\.(save|create|insert)\w*\([^)]*event|INSERT INTO\s+\w*event|raw_?payload|store_?event|persist)",
    "ordering": r"(sequence|created_at\s*[<>]|occurred_at|event_?time|\bversion\b|out.of.order|updated_at\s*[<>]|ALLOWED_TRANSITIONS|can_transition|valid_transitions|transition)",
    "idem": r"(idempoten|Idempotency-Key|dedup(e|lication)?_?key)",
    "lock": r"(select_for_update|with_for_update|atomic|@Transactional|\block\b|FOR UPDATE|version\s*=|isolation|findOneAndUpdate|BEGIN)",
    "status_read": r"\bstatus\b\s*(==|!=|in\b|===)",
    "status_write": r"(\.status\s*=[^=]|status\s*=\s*['\"]|\.update\([^)]*status|\bstatus:\s*['\"]|SET\s+status)",
    "transition_guard": r"(ALLOWED_TRANSITIONS|VALID_TRANSITIONS|can_transition|allowed_from|state_?machine|transitions?\b)",
    "refund_limit": r"(refunded|refundable|remaining|amount_?refunded|amount\w*\s*(<=|>=|>|<)\s*\w*(captured|paid|total|charge))",
    "pending": r"(pending|processing|requires_action|reconcil|requires_capture)",
    "float_money": r"(float\([^)]*amount|amount\s*:\s*float|\bFloat\(|parseFloat\([^)]*amount|double\s+amount)",
    "resp_check": r"(raise_for_status|status_code|\.ok\b|response\.status|res\.status|isSuccess|\.status\(\))",
    "mutate": r"(\.save\(|\.commit\(|\.update\(|\.insert|\.create\(|execute\(|INSERT|UPDATE\s+\w+\s+SET|repository\.save|\.add\()",
    "body_in": r"(get_json\(|request\.json|await\s+request\.json|req\.body|@RequestBody|request\.data|\.json\(\))",
    "own_check": r"(user_id|owner|request\.user|current_user|tenant|org_id|account_id|req\.user|principal|@PreAuthorize)",
    "ssrf_guard": r"(allowlist|allowed_hosts|is_private|ipaddress|ALLOWED_|whitelist|urlparse\([^)]*\)\.(hostname|netloc))",
}
CALL_TIMEOUT = re.compile(r"requests\.(get|post|put|patch|delete|request)\(", re.I)


def feats(segs: list[Seg]) -> dict:
    return {k: find(segs, v) for k, v in RX.items()}


def provider_calls_without_timeout(segs: list[Seg]) -> Optional[tuple]:
    for f, start, text in segs:
        for m in CALL_TIMEOUT.finditer(text):
            stmt = text[m.start():m.start() + 500]
            depth, end = 0, len(stmt)
            for i, ch in enumerate(stmt):
                depth += ch == "("
                depth -= ch == ")"
                if depth == 0 and i > 0:
                    end = i
                    break
            if "timeout" not in stmt[:end + 1]:
                ln = text[:m.start()].count("\n")
                return (f, start + ln, text.splitlines()[ln].strip()[:200])
    return None


def head(segs: list[Seg], n: int = 6) -> str:
    return "\n".join(segs[0][2].splitlines()[:n]) if segs else ""


# --------------------------------------------------------------------------- webhook analyzer
WH_ROUTE = re.compile(r"(webhook|/hooks?\b|callback|/ipn\b|notify)", re.I)
WH_BODY = re.compile(r"(construct_?[eE]vent|Stripe-Signature|X-Razorpay-Signature|X-Hub-Signature|webhook)", re.I)
PAY_CRIT = re.compile(r"(pay|charge|refund|order|subscription|invoice|stripe|razorpay|paypal|checkout)", re.I)


def is_webhook(ep: dict) -> bool:
    if ep["method"] != "POST" or ep["source"] != "code":
        return False
    body = "\n".join(t for _, _, t in ep["_segs"][:1])
    return bool(WH_ROUTE.search(ep["path"]) or WH_BODY.search(body))


def analyze_webhooks(ctx: Ctx) -> None:
    dlq = re.search(r"(dead.?letter|\bdlq\b|failed_?events?|retry_?count|max_?attempts|quarantine)", ctx.repo_text, re.I)
    for ep in ctx.endpoints:
        if not is_webhook(ep):
            continue
        segs = ep["_segs"]
        F = feats(segs)
        file_all = next((s.text for s in ctx.srcs if s.rel == ep["file"]), "")
        file_has_verify = bool(re.search(RX["verify"], file_all, re.I))
        critical = bool(PAY_CRIT.search(ep["path"] + head(segs, 30)))
        ln = ep["line"]
        scope = f"{seg_lines(segs)} lines across {len(segs)} segment(s) analyzed"
        consumes = bool(F["body_in"] or ep["validation"] != "n/a" or ep["params"])
        mutates = bool(F["mutate"])
        corroborated = consumes and mutates and ep["auth"] != "present"
        ep["features"] = {k: bool(v) for k, v in F.items()}
        ep["features"].update({"webhook": True, "dlq_repo": bool(dlq)})
        checks = {
            "signature": "present" if F["verify"] else "absent", "timestamp": "present" if F["timestamp"] else "absent",
            "dedupe": "present" if F["dedupe"] else "absent", "async_ack": "present" if (F["async"] or not F["heavy"]) else "absent",
            "persistence": "present" if F["persist"] else "absent", "ordering": "present" if F["ordering"] else "absent",
            "dead_letter": "present" if dlq else "absent",
        }
        ev = {k: (f"{v[0]}:{v[1]}" if v else None) for k, v in {"signature": F["verify"], "timestamp": F["timestamp"], "dedupe": F["dedupe"], "persistence": F["persist"], "ordering": F["ordering"]}.items()}
        # 1. signature
        sim = {"scenario": "invalid_signature", "target": f"scan:{ctx.scan_id}:{ep['id']}"}
        if not F["verify"]:
            ctx.add(rule="webhook.no_signature_verification", category="webhook", severity="critical" if critical else "high",
                    confidence="high" if corroborated else "medium", ep=ep, title="Webhook handler does not verify a provider signature",
                    file=ep["file"], line=ln, evidence=head(segs), kind="absence",
                    note=("corroborated: handler parses the request body and mutates state with no auth" if corroborated else "") + f" | scope: {scope}"
                         + ("; a verification routine exists elsewhere in the file but is not reached from this handler" if file_has_verify else ""),
                    expected="Raw body is HMAC-verified against the provider secret before any processing",
                    actual="Any caller can post forged events; forged payloads reach business logic",
                    fix="Verify the signature over the RAW request body with a constant-time compare (or the provider SDK's construct_event) and return 400/401 before parsing.",
                    tests=["Invalid signature -> 4xx and no side effects", "Valid signature -> 2xx", "Body mutated after signing -> rejected"],
                    repro="POST a synthetic event with a missing/incorrect signature header to the handler in a sandbox; observe whether it is processed.", sim=sim)
        elif not F["const_time"]:
            unsafe = find(segs, r"(sig\w*|signature\w*|digest\w*|expected\w*|computed\w*)\s*(==|!=)\s*(?!None|null|undefined|\"\"|'')\w|(\w|\))\s*(==|!=)\s*(sig\w*|signature\w*|digest\w*|expected\w*|computed\w*)")
            if unsafe:
                ctx.add(rule="webhook.timing_unsafe_compare", category="webhook", severity="medium", confidence="high", ep=ep,
                        title="Signature compared with a non-constant-time operator", file=unsafe[0], line=unsafe[1], evidence=unsafe[2],
                        expected="hmac.compare_digest / timingSafeEqual", actual="Early-exit string comparison leaks signature prefix timing",
                        fix="Use hmac.compare_digest (Python), crypto.timingSafeEqual (Node) or MessageDigest.isEqual (Java).",
                        tests=["Unit test asserts constant-time comparator is used"], repro="Static: the comparison uses ==/!= on a signature value.")
        # 2. timestamp/replay
        if F["verify"] and not F["timestamp"]:
            ctx.add(rule="webhook.no_timestamp_validation", category="webhook", severity="high" if critical else "medium", confidence="medium", ep=ep,
                    title="Signed webhook has no timestamp tolerance (replay window is unbounded)", file=F["verify"][0], line=F["verify"][1],
                    evidence=F["verify"][2], kind="absence", note=f"scope: {scope}",
                    expected="Signed timestamp must be within a tolerance (e.g. 5 min) and be part of the signed payload",
                    actual="A captured valid request can be replayed indefinitely" + ("" if not F["dedupe"] else " (duplicate store may limit impact)"),
                    fix="Include the timestamp in the signed string and reject if |now - ts| exceeds the tolerance.",
                    tests=["Expired timestamp -> rejected", "Timestamp removed -> rejected"], repro="Replay a previously valid synthetic delivery after the tolerance elapses.",
                    sim={"scenario": "expired_timestamp", "target": f"scan:{ctx.scan_id}:{ep['id']}"})
        # 3. dedupe
        if not F["dedupe"] and mutates:
            ctx.add(rule="webhook.no_duplicate_handling", category="webhook", severity="high" if critical else "medium",
                    confidence="high" if (critical and corroborated) else "medium", ep=ep, kind="absence",
                    title="Webhook processing is not deduplicated by event id", file=ep["file"], line=ln, evidence=head(segs),
                    note=(f"corroborated: handler mutates state ({F['mutate'][2]})" if corroborated else "") + f" | scope: {scope}",
                    expected="Provider delivery is at-least-once, so processing must be idempotent (event id stored with a unique constraint)",
                    actual="Redelivery applies side effects again (double credit, double fulfilment)",
                    fix="Persist event.id with a UNIQUE constraint in the same transaction as the side effect; treat conflict as already-processed and return 2xx.",
                    tests=["Same event id delivered 3x -> exactly one side effect", "Concurrent duplicates -> one side effect"],
                    repro="Deliver the same synthetic event twice; compare resulting state.",
                    sim={"scenario": "duplicate_webhook", "target": f"scan:{ctx.scan_id}:{ep['id']}"})
        elif F["dedupe"] and not F["atomic"]:
            ctx.add(rule="webhook.dedupe_not_atomic", category="webhook", severity="low", confidence="low", ep=ep, kind="absence",
                    title="Duplicate check may be a check-then-act race", file=F["dedupe"][0], line=F["dedupe"][1], evidence=F["dedupe"][2],
                    expected="Atomic claim of the event id (unique constraint / SETNX)", actual="No atomic primitive seen near the duplicate check; concurrent deliveries may both pass",
                    fix="Replace read-then-write with INSERT ... ON CONFLICT DO NOTHING or SETNX.", tests=["Concurrent duplicate delivery test"],
                    repro="Send two simultaneous deliveries of one event.",
                    sim={"scenario": "duplicate_webhook", "target": f"scan:{ctx.scan_id}:{ep['id']}", "params": {"concurrent": True}})
        ep["features"]["dedupe_atomic"] = bool(F["atomic"] or not F["dedupe"])
        # 4. ack / timeout
        if F["heavy"] and not F["async"]:
            ctx.add(rule="webhook.sync_heavy_work", category="webhook", severity="medium", confidence="medium", ep=ep,
                    title="Handler performs slow external work before acknowledging", file=F["heavy"][0], line=F["heavy"][1], evidence=F["heavy"][2],
                    expected="Persist + enqueue, acknowledge within seconds, process asynchronously",
                    actual="Provider timeouts trigger redelivery while the first attempt is still running",
                    fix="Store the raw event, return 2xx immediately, process from a queue/worker.",
                    tests=["Slow downstream -> handler still acks < 2s"], repro="Delay the downstream call in a sandbox and observe response latency.",
                    sim={"scenario": "delayed_response", "target": f"scan:{ctx.scan_id}:{ep['id']}"})
        # 5. swallowed errors
        sw = find(segs, r"except[^\n]*:\s*\n(?:[ \t]+[^\n]*\n){0,4}?[ \t]+return[^\n]*(200|ok|received|success)|catch\s*\([^)]*\)\s*\{[^{}]*res\.(sendStatus|status)\(\s*200")
        if sw:
            ctx.add(rule="webhook.ack_on_failure", category="webhook", severity="high", confidence="medium", ep=ep,
                    title="Handler acknowledges success after catching an error", file=sw[0], line=sw[1], evidence=sw[2],
                    expected="Failures return 5xx so the provider retries, or the event is persisted for replay",
                    actual="Failed events are acknowledged and permanently lost",
                    fix="Return 5xx on processing failure or persist the event to a dead-letter store before acknowledging.",
                    tests=["Downstream failure -> 5xx or event persisted"], repro="Inject an exception into processing; observe the HTTP status.",
                    sim={"scenario": "retry_exhaustion", "target": f"scan:{ctx.scan_id}:{ep['id']}", "profile": {"persists_events": False}})
            ep["features"]["swallows_errors"] = True
        # 6. persistence / DLQ
        if not F["persist"] and mutates:
            ctx.add(rule="webhook.no_event_persistence", category="webhook", severity="medium", confidence="low", ep=ep, kind="absence",
                    title="No evidence raw events are persisted before processing", file=ep["file"], line=ln, evidence=head(segs, 4), note=scope,
                    expected="Raw event stored first so crashes can be recovered/replayed", actual="Recovery depends entirely on provider redelivery",
                    fix="Insert the raw payload + id + received_at in an events table before processing; add a replay job.",
                    tests=["Crash mid-processing -> event replayable"], repro="Kill the worker mid-handler in a sandbox.",
                    sim={"scenario": "retry_exhaustion", "target": f"scan:{ctx.scan_id}:{ep['id']}"})
        if not dlq and mutates:
            ctx.add(rule="webhook.no_dead_letter", category="webhook", severity="low", confidence="low", ep=ep, kind="absence",
                    title="No dead-letter / failed-event handling found in the repository", file=ep["file"], line=ln, evidence=head(segs, 3),
                    note="repo-wide search; handling may live outside this repository", expected="Events that exhaust retries land in a dead-letter store with alerting",
                    actual="Exhausted events are dropped", fix="Add a failed_events/DLQ table with retry_count and an operator replay path.",
                    tests=["Poison event ends in dead-letter, not dropped"], repro="Static repository search.")
        # 7. ordering
        if F["status_write"] and not F["ordering"]:
            ctx.add(rule="webhook.no_ordering_guard", category="webhook", severity="medium", confidence="medium", ep=ep, kind="absence",
                    title="Status updated from events with no ordering guard", file=F["status_write"][0], line=F["status_write"][1], evidence=F["status_write"][2],
                    note=scope, expected="Events carry/compare sequence, version or created time; stale events are ignored",
                    actual="A late 'authorized' event can overwrite 'captured'/'refunded'",
                    fix="Compare event created/sequence to the stored last-applied marker or enforce a state machine.",
                    tests=["Events delivered in reverse order -> final state correct"], repro="Deliver captured then authorized events out of order.",
                    sim={"scenario": "out_of_order", "target": f"scan:{ctx.scan_id}:{ep['id']}"})
        ep["features"]["orders_events"] = bool(F["ordering"] or not F["status_write"])
        ctx.webhooks.append({"id": f"WH-{len(ctx.webhooks) + 1:02d}", "endpoint_id": ep["id"], "ref": f"{ep['method']} {ep['path']}", "file": ep["file"],
                             "line": ln, "source": "code", "checks": checks, "evidence": ev,
                             "status": "verified" if checks["signature"] == "present" and checks["dedupe"] == "present" else ("partial" if checks["signature"] == "present" else "unverified")})
    return None


# --------------------------------------------------------------------------- payment analyzer
PAY_ROUTE = re.compile(r"(pay|charge|checkout|refund|capture|authori[sz]e|payout|transaction|void|intent)", re.I)
PAY_BODY = re.compile(r"(PaymentIntent|charges?\.create|razorpay|paypal|adyen|braintree|stripe\.|\.refund|payment_?gateway|gateway\.)", re.I)


def analyze_payments(ctx: Ctx) -> None:
    for ep in ctx.endpoints:
        if ep["source"] != "code" or ep["method"] not in MUTATING or ep.get("features", {}).get("webhook"):
            continue
        segs = ep["_segs"]
        txt = "\n".join(t for _, _, t in segs)
        if not (PAY_ROUTE.search(ep["path"]) or PAY_BODY.search(txt)):
            continue
        sub = "refund" if "refund" in ep["path"].lower() else "capture" if "capture" in ep["path"].lower() else "payment"
        F = feats(segs)
        tgt = f"scan:{ctx.scan_id}:{ep['id']}"
        ep["features"] = {k: bool(v) for k, v in F.items()}
        ep["features"].update({"payment": True, "subtype": sub, "idempotency_key": bool(F["idem"])})
        provider = bool(PAY_BODY.search(txt) or re.search(r"(requests|httpx|axios|fetch)", txt))
        base = dict(ep=ep, category="payment")
        if not F["idem"] and sub in ("payment", "refund"):
            ctx.add(rule=f"payment.{sub}_no_idempotency", severity="high", confidence="high" if provider else "medium", kind="absence",
                    title=f"{sub.title()} endpoint has no idempotency protection", file=ep["file"], line=ep["line"], evidence=head(segs),
                    note=("corroborated: calls payment provider" if provider else "") + f" | scope: {seg_lines(segs)} lines", **base,
                    expected="Client-supplied Idempotency-Key is stored and replays return the original result",
                    actual="A client/network retry after an ambiguous timeout creates a second " + ("refund" if sub == "refund" else "charge"),
                    fix="Require an Idempotency-Key header, store (key, request hash, response) under a UNIQUE constraint before calling the provider, and forward the key to the provider.",
                    tests=["Same key twice -> one provider call, identical response", "Same key different body -> 409/422", "Timeout then retry -> single charge"],
                    repro="Send the same synthetic request twice (or timeout then retry) against a sandbox provider; count resulting transactions.",
                    sim={"scenario": "network_timeout", "target": tgt})
        t = provider_calls_without_timeout(segs)
        if t:
            ctx.add(rule="payment.provider_call_no_timeout", severity="high", confidence="high", title="Provider HTTP call has no timeout",
                    file=t[0], line=t[1], evidence=t[2], **base, expected="Every outbound provider call sets connect/read timeouts",
                    actual="A hung provider holds workers indefinitely; callers retry into unknown state",
                    fix="Set explicit timeout=(connect, read) and treat timeouts as 'unknown' state requiring reconciliation, not failure.",
                    tests=["Provider delay > timeout -> pending state + reconciliation"], repro="Delay provider response beyond timeout in a sandbox.",
                    sim={"scenario": "delayed_response", "target": tgt})
        if F["status_read"] and F["status_write"] and not F["lock"]:
            ctx.add(rule="payment.status_race", severity="high", confidence="medium", kind="absence", title="Check-then-update on payment status without locking",
                    file=F["status_write"][0], line=F["status_write"][1], evidence=f"{F['status_read'][2]}  ...  {F['status_write'][2]}", **base,
                    note=f"read at line {F['status_read'][1]}, write at line {F['status_write'][1]}",
                    expected="Row lock, optimistic version or conditional UPDATE guards the transition", actual="Concurrent requests can both pass the status check (double capture / refund)",
                    fix="Use SELECT ... FOR UPDATE, a version column, or UPDATE ... WHERE status='authorized' and check affected rows.",
                    tests=["Two concurrent captures -> one succeeds", "Concurrent refunds never exceed captured amount"],
                    repro="Fire two simultaneous synthetic requests at a sandbox instance.", sim={"scenario": "duplicate_webhook", "target": tgt, "params": {"concurrent": True}})
        if F["status_write"] and not F["transition_guard"]:
            ctx.add(rule="payment.no_state_machine", severity="medium", confidence="low", kind="absence", title="Status assigned without an explicit transition map",
                    file=F["status_write"][0], line=F["status_write"][1], evidence=F["status_write"][2], **base,
                    expected="Only valid transitions (e.g. authorized->captured->refunded) are permitted", actual="Any status can overwrite any other",
                    fix="Introduce an allowed-transition table and reject invalid moves.", tests=["Invalid transition -> rejected"],
                    repro="Attempt refunded->authorized on a synthetic record.", sim={"scenario": "out_of_order", "target": tgt})
        if sub == "refund" and not F["refund_limit"]:
            ctx.add(rule="payment.refund_no_limit_check", severity="high", confidence="medium", kind="absence", title="Refund does not appear to bound amount by refundable balance",
                    file=ep["file"], line=ep["line"], evidence=head(segs), note=f"scope: {seg_lines(segs)} lines", **base,
                    expected="refund_amount <= captured - already_refunded, enforced transactionally", actual="Repeated or oversized refunds may exceed the captured amount",
                    fix="Track amount_refunded; reject when requested > refundable, inside the same transaction as the provider call record.",
                    tests=["Refund > captured -> 422", "Two partial refunds summing > captured -> second rejected"], repro="Issue two refunds totalling more than captured in a sandbox.")
        if F["float_money"]:
            ctx.add(rule="payment.float_money", severity="medium", confidence="high", title="Monetary amount handled as floating point",
                    file=F["float_money"][0], line=F["float_money"][1], evidence=F["float_money"][2], **base, expected="Integer minor units or Decimal",
                    actual="Binary floats introduce rounding drift (0.1+0.2) and reconciliation mismatches", fix="Use integer minor units or Decimal end to end.",
                    tests=["Property test: sum of parts equals total for random amounts"], repro="Static: float conversion of an amount value.")
        if provider and re.search(r"(requests|httpx)\.\w+\(", txt) and not F["resp_check"]:
            ctx.add(rule="payment.provider_response_unchecked", severity="medium", confidence="medium", kind="absence",
                    title="Provider response used without checking status", file=ep["file"], line=ep["line"], evidence=head(segs), **base,
                    note=f"scope: {seg_lines(segs)} lines", expected="HTTP status and provider status checked; unknown outcomes are reconciled",
                    actual="Error or malformed provider responses may be recorded as success", fix="Call raise_for_status(), validate the response schema, map provider statuses explicitly.",
                    tests=["Provider 500/malformed body -> payment not marked paid"], repro="Return a malformed provider response in a sandbox.",
                    sim={"scenario": "malformed_payload", "target": tgt, "params": {"kind": "response"}})
        if provider and F["mutate"] and not F["pending"]:
            ctx.add(rule="payment.no_pending_state", severity="medium", confidence="low", kind="absence",
                    title="No pending/unknown state around the provider call (partial-failure risk)", file=ep["file"], line=ep["line"], evidence=head(segs, 4), **base,
                    note=f"scope: {seg_lines(segs)} lines", expected="Intent recorded as pending before calling the provider; reconciliation resolves unknowns",
                    actual="A crash or timeout between provider call and DB write loses or duplicates the transaction record",
                    fix="Write a pending record first, call the provider with the idempotency key, then finalise; add a reconciliation job.",
                    tests=["Crash after provider success -> reconciliation finalises"], repro="Fail the DB write after a successful provider call in a sandbox.",
                    sim={"scenario": "connection_interruption", "target": tgt})


# --------------------------------------------------------------------------- retry & idempotency analyzer
def analyze_retries(ctx: Ctx) -> None:
    def rec(src: Src, line: int, lib: str, attempts: Optional[int], backoff: str, jitter: Optional[bool], bounded: bool, win: str, builtin_jitter: bool = False):
        ctx.retries.append({"file": src.rel, "line": line, "library": lib, "max_attempts": attempts, "backoff": backoff, "jitter": jitter,
                            "bounded": bounded, "snippet": redact(src.lines[line - 1].strip())[:160]})
        snip = src.lines[line - 1].strip()
        if not bounded:
            ctx.add(rule="retry.unbounded", category="retry_idempotency", severity="high", confidence="medium", title="Retry loop has no attempt limit",
                    file=src.rel, line=line, evidence=snip, expected="Bounded retries with a deadline and failure path", actual="Persistent failure retries forever (retry storm, resource exhaustion)",
                    fix="Cap attempts (3-5), add a total deadline, and surface/dead-letter the failure.", tests=["Permanent failure stops after N attempts"],
                    repro="Make the dependency fail permanently in a sandbox.", sim={"scenario": "retry_exhaustion", "target": "reference", "profile": {"max_retries": 50, "backoff": "none"}})
        elif backoff in ("none", "fixed"):
            ctx.add(rule="retry.no_backoff", category="retry_idempotency", severity="low", confidence="medium", title="Retries use no/constant backoff",
                    file=src.rel, line=line, evidence=snip, expected="Exponential backoff with jitter", actual="Synchronised retries amplify outages (thundering herd)",
                    fix="Use exponential backoff (base*2^n, capped) with full jitter.", tests=["Retry delays grow and are randomised"], repro="Static: delay is constant or absent.",
                    sim={"scenario": "provider_unavailable", "target": "reference", "profile": {"backoff": "none", "jitter": False}})
        elif backoff == "exponential" and jitter is False and not builtin_jitter:
            ctx.add(rule="retry.no_jitter", category="retry_idempotency", severity="low", confidence="medium", kind="absence", title="Exponential backoff without jitter",
                    file=src.rel, line=line, evidence=snip, expected="Randomised delay", actual="Clients retry in lockstep after an outage",
                    fix="Add full jitter (random(0, delay)).", tests=["Jitter spreads retries across the window"], repro="Static: no jitter in retry configuration.",
                    sim={"scenario": "provider_unavailable", "target": "reference", "profile": {"jitter": False}})
        if re.search(r"(\.post\(|charge|payment|refund|create_?order|transfer)", win, re.I) and not re.search(RX["idem"], win, re.I):
            ctx.add(rule="retry.non_idempotent", category="retry_idempotency", severity="high", confidence="medium", kind="absence",
                    title="Automatic retry wraps a non-idempotent operation with no idempotency key", file=src.rel, line=line, evidence=snip,
                    note="scope: retry region +/-25 lines", expected="Only idempotent operations (or those carrying an idempotency key) are retried",
                    actual="Retry after an ambiguous timeout can execute the operation twice",
                    fix="Send a stable Idempotency-Key per logical operation (generated once, reused on every attempt) or disable retries for this call.",
                    tests=["Timeout-then-retry produces one effect"], repro="Delay the response past the timeout; count side effects.",
                    sim={"scenario": "network_timeout", "target": "naive"})
        if re.search(r"idempot\w*\s*[:=][^\n]*(uuid|random|time\.time|Date\.now)", win, re.I) and re.search(r"(for\s|while\s)", win):
            m = find([(src.rel, max(1, line - 25), win)], r"idempot\w*\s*[:=][^\n]*(uuid|random|time\.time|Date\.now)")
            ctx.add(rule="retry.key_regenerated", category="retry_idempotency", severity="high", confidence="medium", title="Idempotency key may be regenerated on each attempt",
                    file=src.rel, line=m[1] if m else line, evidence=m[2] if m else snip, note="key assignment appears inside the retry region",
                    expected="Key is generated once per logical operation", actual="Each retry carries a new key, defeating deduplication",
                    fix="Generate the key before the loop and reuse it for all attempts.", tests=["All attempts of one operation share the same key"],
                    repro="Static: key assignment with uuid/random inside loop.", sim={"scenario": "network_timeout", "target": "naive"})

    for s in ctx.srcs:
        if s.ext not in (".py", ".js", ".ts", ".java", ".kt"):
            continue
        t, L = s.text, s.lines
        win_of = lambda ln: "\n".join(L[max(0, ln - 26):ln + 25])
        if s.ext == ".py":
            for m in re.finditer(r"@retry\((.*?)\)\s*\n\s*(?:async\s+)?def", t, re.S):
                a = m.group(1)
                ln = t[:m.start()].count("\n") + 1
                att = re.search(r"stop_after_attempt\((\d+)\)", a)
                bk = "exponential" if "wait_exponential" in a or "wait_random_exponential" in a else ("fixed" if re.search(r"wait_fixed|wait_none|wait\s*=\s*\d", a) else "none")
                jit = bool(re.search(r"wait_random|jitter", a))
                rec(s, ln, "tenacity", int(att.group(1)) if att else None, bk, jit, bool(att), win_of(ln), builtin_jitter=jit)
            for m in re.finditer(r"\bRetry\(([^)]*)\)", t):
                a = m.group(1)
                ln = t[:m.start()].count("\n") + 1
                tot = re.search(r"total\s*=\s*(\d+)", a)
                bf = re.search(r"backoff_factor\s*=\s*([\d.]+)", a)
                rec(s, ln, "urllib3.Retry", int(tot.group(1)) if tot else None, "exponential" if bf and float(bf.group(1)) > 0 else "none",
                    bool(re.search("backoff_jitter", a)), bool(tot), win_of(ln))
                if re.search(r"allowed_methods[^)]*POST", a):
                    ctx.add(rule="retry.post_allowed", category="retry_idempotency", severity="medium", confidence="high", title="HTTP client retries POST requests",
                            file=s.rel, line=ln, evidence=L[ln - 1].strip(), expected="POST retried only with idempotency keys", actual="Non-idempotent POSTs are retried on 5xx/timeouts",
                            fix="Remove POST from allowed_methods or send idempotency keys.", tests=["POST timeout -> not blindly retried"], repro="Static configuration.")
            for m in re.finditer(r"^([ \t]*)(for\s+\w+\s+in\s+range\(([^)]*)\)|while\s+(True|1))\s*:", t, re.M):
                ln = t[:m.start()].count("\n") + 1
                body = "\n".join(L[ln - 1:ln + 16])
                if not (re.search(r"sleep\(", body) and re.search(r"except", body)):
                    continue
                unbounded = bool(m.group(4)) and not re.search(r"\b(break|return|raise)\b", body)
                bk = "exponential" if re.search(r"(\*\*|\*=|2\s*\*)", body) else ("fixed" if "sleep(" in body else "none")
                n = re.fullmatch(r"\s*(\d+)\s*", m.group(3) or "")
                rec(s, ln, "manual loop", int(n.group(1)) if n else None, bk, bool(re.search(r"random|uniform|jitter", body)), not unbounded, win_of(ln))
        elif s.ext in (".js", ".ts"):
            for m in re.finditer(r"axiosRetry\(\s*\w+\s*,\s*\{([^}]*)\}", t, re.S):
                a = m.group(1)
                ln = t[:m.start()].count("\n") + 1
                r_ = re.search(r"retries\s*:\s*(\d+)", a)
                rec(s, ln, "axios-retry", int(r_.group(1)) if r_ else 3, "exponential" if "exponentialDelay" in a else "none", True if "exponentialDelay" in a else None,
                    True, win_of(ln), builtin_jitter=True)
        else:
            for m in re.finditer(r"@Retryable\(([^)]*(?:\([^)]*\)[^)]*)*)\)", t):
                a = m.group(1)
                ln = t[:m.start()].count("\n") + 1
                att = re.search(r"maxAttempts\s*=\s*(\d+)", a)
                mult = re.search(r"multiplier\s*=\s*([\d.]+)", a)
                rec(s, ln, "spring-retry", int(att.group(1)) if att else 3, "exponential" if mult else ("fixed" if "delay" in a else "none"), bool(re.search(r"random\s*=\s*true", a)), True, win_of(ln))
        for m in re.finditer(r"(?i)\b(seen_keys|idempotency_keys?|processed_ids|idem_store)\s*[:=][^\n]*(\{\}|set\(\)|dict\(\)|\[\]|new Map\(\)|new Set\(\))", t):
            ln = t[:m.start()].count("\n") + 1
            ctx.add(rule="idempotency.in_memory_store", category="retry_idempotency", severity="medium", confidence="medium", title="Idempotency/dedupe state held in process memory",
                    file=s.rel, line=ln, evidence=L[ln - 1].strip(), expected="Durable shared store (DB unique constraint / Redis SETNX with TTL)",
                    actual="Keys are lost on restart and not shared across workers; duplicates pass after deploy or on another replica",
                    fix="Persist keys in the database with a unique constraint (or Redis SET NX EX).", tests=["Restart between duplicate requests -> still deduplicated"],
                    repro="Static: module-level in-memory collection used as key store.", sim={"scenario": "duplicate_webhook", "target": "naive", "params": {"concurrent": True}})
        for m in re.finditer(r"(?i)exactly[- ]once", t):
            ln = t[:m.start()].count("\n") + 1
            ctx.add(rule="idempotency.exactly_once_claim", category="retry_idempotency", severity="low", confidence="high", title="Code/comment claims exactly-once processing",
                    file=s.rel, line=ln, evidence=L[ln - 1].strip(), expected="Documentation states at-least-once delivery with idempotent processing",
                    actual="Network delivery cannot be exactly-once; the claim hides the need for deduplication",
                    fix="Reword to 'at-least-once delivery, idempotent processing' and verify a dedupe store exists.", tests=["Duplicate delivery test"], repro="Static: comment text.")
    for ep in ctx.endpoints:
        f = ep.get("features") or {}
        if f.get("payment") or f.get("webhook"):
            ctx.idem.append({"endpoint": f"{ep['method']} {ep['path']}", "endpoint_id": ep["id"], "kind": "webhook" if f.get("webhook") else f.get("subtype", "payment"),
                             "idempotency_key": bool(f.get("idem") or f.get("idempotency_key")), "event_dedupe": bool(f.get("dedupe")),
                             "atomic": bool(f.get("dedupe_atomic", f.get("atomic"))), "file": ep["file"], "line": ep["line"],
                             "delivery_model": "at-least-once + idempotent processing" if (f.get("dedupe") or f.get("idem")) else "at-least-once, NOT idempotent"})


# --------------------------------------------------------------------------- security analyzer
def analyze_security(ctx: Ctx) -> None:
    limiter = re.search(r"(slowapi|flask_limiter|Limiter\(|throttle|rateLimit|express-rate-limit|bucket4j|RateLimiter|@RateLimit|limit_req)", ctx.repo_text)
    for ep in ctx.endpoints:
        if ep["source"] != "code":
            continue
        segs, M, P = ep["_segs"], ep["method"], ep["path"]
        txt = "\n".join(t for _, _, t in segs)
        F = feats(segs) if segs else {}
        wh = ep.get("features", {}).get("webhook")
        tgt = {"scenario": "http_status", "target": f"scan:{ctx.scan_id}:{ep['id']}"}
        # authentication
        if ep["auth"] == "absent" and not PUBLIC_ROUTE.search(P):
            sensitive = bool(re.search(r"(user|account|admin|payment|invoice|order|customer|profile|billing|refund)", P, re.I))
            explicit = ep["auth_scope"] in ("explicit",)
            if M in MUTATING or sensitive:
                ctx.add(rule="auth.missing", category="security", severity="critical" if (explicit and M in MUTATING and PAY_CRIT.search(P)) else ("high" if M in MUTATING else "medium"),
                        confidence="high" if explicit else ("medium" if M in MUTATING else "low"), ep=ep, kind="presence" if explicit else "absence",
                        title=f"{M} {P} has no authentication evidence", file=ep["file"], line=ep["line"], evidence=f"{ep['auth_evidence']}\n{head(segs, 4)}",
                        note="explicit permissive config" if explicit else "no auth decorator/middleware/dependency in route chain and no global auth hook found; gateway-level auth is not visible to static analysis",
                        expected="Endpoint requires an authenticated principal and an authorization decision", actual="Handler is reachable without a credential check in application code",
                        fix="Add authentication (dependency/middleware/annotation) and enforce authorization before side effects; document intentional public routes.",
                        tests=["No credential -> 401", "Insufficient role -> 403"], repro="Call the endpoint in a sandbox without credentials.", sim=tgt)
        # BOLA
        if re.search(r"\{\w*id\w*\}", P, re.I) and ep["auth"] != "absent" and M != "OPTIONS" and re.search(r"(get\(|filter|find|objects|query|select|repository|findById)", txt, re.I) and not F.get("own_check"):
            ctx.add(rule="authz.object_level_candidate", category="security", severity="medium", confidence="low", ep=ep, kind="absence",
                    title="Object lookup by id with no visible ownership/tenant check (BOLA candidate)", file=ep["file"], line=ep["line"], evidence=head(segs, 5),
                    note="heuristic candidate - verify manually", expected="Record access is scoped to the caller (owner/tenant) or checked after lookup",
                    actual="Authenticated users may access other users' records by guessing ids", fix="Scope queries by principal/tenant or verify ownership after lookup; return 404 on mismatch.",
                    tests=["User A cannot read/modify user B's object"], repro="Use two synthetic users in a sandbox; request B's id as A.", sim=tgt)
        # validation
        if M in MUTATING and ep["validation"] == "none" and not wh:
            ctx.add(rule="validation.weak_body", category="validation", severity="medium", confidence="medium", ep=ep,
                    title="Request body consumed without schema validation", file=ep["file"], line=ep["line"], evidence=f"{F['body_in'][2] if F.get('body_in') else head(segs, 3)}",
                    expected="Body validated against a typed schema with bounds", actual="Unvalidated input reaches business logic (type confusion, oversized fields, mass assignment)",
                    fix="Validate with Pydantic/Marshmallow/Zod/Bean Validation (@Valid), set max sizes, reject unknown fields.", tests=["Malformed/oversized body -> 400/422"],
                    repro="Send malformed synthetic JSON in a sandbox.", sim={"scenario": "malformed_payload", "target": f"scan:{ctx.scan_id}:{ep['id']}", "params": {"kind": "request"}})
        elif M in MUTATING and ep["validation"] == "partial":
            ctx.add(rule="validation.no_valid_annotation", category="validation", severity="low", confidence="low", ep=ep, kind="absence",
                    title="@RequestBody without @Valid/@Validated", file=ep["file"], line=ep["line"], evidence=head(segs, 3), note="DTO constraints may be validated elsewhere",
                    expected="Bean Validation triggered at the controller boundary", actual="Constraint annotations (if any) are not enforced",
                    fix="Add @Valid to the parameter and constraints to the DTO.", tests=["Invalid DTO -> 400"], repro="Static.")
        # rate limiting (absence only -> capped confidence)
        if not limiter and re.search(r"(login|token|auth|password|payment|charge|refund|otp|sms)", P, re.I) and M in MUTATING and not wh:
            ctx.add(rule="ratelimit.none_found", category="rate_limit", severity="medium", confidence="low", ep=ep, kind="absence",
                    title="No rate limiting found for a sensitive endpoint", file=ep["file"], line=ep["line"], evidence=head(segs, 3),
                    note="no limiter library/config in repository; may be enforced by gateway/WAF", expected="Per-client throttling on credential and payment endpoints",
                    actual="Brute force / card-testing is not throttled in application code", fix="Add a limiter (per IP + per account) and return 429 with Retry-After.",
                    tests=["Burst of requests -> 429"], repro="Burst synthetic requests against a sandbox instance.", sim={"scenario": "http_status", "target": tgt["target"], "params": {"status": 429}})
        # errors
        er = find(segs, r"(traceback\.format_exc|str\(e\)|str\(exc\)|e\.getMessage\(\)|err\.stack|printStackTrace|res\.(status\(\d+\)\.)?(send|json)\(\s*(err|error)\b)") if segs else None
        if er and re.search(r"(return|detail\s*=|res\.|ResponseEntity|jsonify|JSONResponse|HTTPException)", er[2]):
            ctx.add(rule="errors.leaky", category="error_handling", severity="medium", confidence="medium", ep=ep, title="Exception details returned to the client",
                    file=er[0], line=er[1], evidence=er[2], expected="Generic error body + correlation id; details logged server-side",
                    actual="Stack traces / internal messages disclose implementation details", fix="Return a generic message with a request id; log the exception.",
                    tests=["Forced exception -> response has no stack/internal text"], repro="Trigger an exception with a synthetic input.", sim={"scenario": "malformed_payload", "target": tgt["target"]})
        # SSRF callbacks
        cb = find(segs, r"(callback_?url|webhook_?url|notify_?url|return_?url|redirect_?url)") if segs else None
        if cb and F.get("heavy") and not F.get("ssrf_guard"):
            ctx.add(rule="ssrf.callback", category="security", severity="medium", confidence="low", ep=ep, kind="absence",
                    title="Outbound call to a caller-supplied callback URL with no allow-list evidence", file=cb[0], line=cb[1], evidence=cb[2],
                    note=f"outbound call at {F['heavy'][0]}:{F['heavy'][1]}", expected="Callback hosts validated against an allow-list; private ranges blocked",
                    actual="Server may be induced to call internal addresses (SSRF)", fix="Validate scheme/host against an allow-list, resolve and block private/link-local IPs, disable redirects.",
                    tests=["Callback to 169.254.169.254/localhost -> rejected"], repro="Supply an internal URL in a sandbox and observe the outbound target.")
        # sensitive logging
        lg = find(segs, r"(log(ger)?\.\w+|console\.log|print)\([^)\n]*(password|passwd|token|card_?number|cvv|secret)") if segs else None
        if lg:
            ctx.add(rule="exposure.sensitive_log", category="security", severity="medium", confidence="medium", ep=ep, title="Sensitive value written to logs",
                    file=lg[0], line=lg[1], evidence=lg[2], expected="Secrets/PANs never logged", actual="Credentials may persist in log storage", fix="Remove or mask the value before logging.",
                    tests=["Log capture contains no secrets"], repro="Static: logging call references a sensitive identifier.")
    # repo-wide: CORS, secrets, TLS verify, debug
    for s in ctx.srcs:
        L, t = s.lines, s.text
        for i, l in enumerate(L):
            w = "\n".join(L[max(0, i - 6):i + 7])
            if re.search(r"allow_origins\s*=\s*\[\s*['\"]\*['\"]", l) or re.search(r"\bcors\(\s*\)", l) or re.search(r"origin\s*:\s*(['\"]\*['\"]|true)", l) or re.search(r"(allowedOrigins\(\s*\"\*\"|@CrossOrigin\([^)]*\*)", l) or re.fullmatch(r"\s*CORS\(\w+\)\s*", l):
                creds = bool(re.search(r"(allow_credentials\s*=\s*True|credentials\s*:\s*true|allowCredentials\(\s*true|supports_credentials\s*=\s*True)", w))
                ctx.add(rule="cors.wildcard_credentials" if creds else "cors.wildcard", category="security", severity="high" if creds else "low",
                        confidence="high" if creds else "medium", title="CORS allows any origin" + (" with credentials" if creds else ""), file=s.rel, line=i + 1, evidence=l.strip(),
                        expected="Explicit origin allow-list" + ("; credentials never combined with wildcard/reflected origins" if creds else ""),
                        actual="Any website can call the API from a browser" + (" using the victim's cookies/credentials" if creds else " (acceptable only for public, credential-less APIs)"),
                        fix="Restrict allowed origins to known front-ends; disable credentials for wildcard policies.", tests=["Disallowed Origin -> no ACAO header"], repro="Send a request with Origin: https://evil.example in a sandbox.")
            if re.search(r"(verify\s*=\s*False|rejectUnauthorized\s*:\s*false|setHostnameVerifier\(.*ALLOW_ALL|InsecureSkipVerify)", l):
                ctx.add(rule="tls.verify_disabled", category="security", severity="medium", confidence="high", title="TLS certificate verification disabled", file=s.rel, line=i + 1,
                        evidence=l.strip(), expected="Certificates verified for all outbound calls", actual="Man-in-the-middle can read/alter provider traffic incl. payment data",
                        fix="Remove the override; trust a custom CA bundle if required.", tests=["Outbound client config test: verify enabled"], repro="Static: TLS verification flag.")
            if re.search(r"(debug\s*=\s*True|app\.run\([^)]*debug\s*=\s*True|DEBUG\s*=\s*True)", l) and s.ext == ".py":
                ctx.add(rule="errors.debug_enabled", category="error_handling", severity="medium", confidence="medium", title="Debug mode enabled in application code", file=s.rel, line=i + 1,
                        evidence=l.strip(), expected="Debug disabled outside local development", actual="Debug pages/tracebacks may be served", fix="Drive DEBUG from environment; default False.",
                        tests=["Production config has debug off"], repro="Static.")
            for name, rx, conf in SECRET_RX:
                m = rx.search(l)
                if not m:
                    continue
                val = m.group(3) if name == "hardcoded_secret" else m.group(0)
                if PLACEHOLDER.search(val) or PLACEHOLDER.search(l) and name == "hardcoded_secret":
                    continue
                low = bool(re.search(r"(example|sample|fixture|mock)", s.rel, re.I))
                ctx.add(rule=f"secret.{name}", category="security", severity="high", confidence="low" if low else conf, title=f"Possible hard-coded secret ({name.replace('_', ' ')})",
                        file=s.rel, line=i + 1, evidence=redact(l.strip()), note="value redacted", expected="Secrets loaded from a secret manager/environment", actual="Credential committed to source control",
                        fix="Rotate the credential immediately, remove from history, load from environment/secret manager, add secret scanning to CI.", tests=["Secret scanner passes in CI"],
                        repro="Static pattern match (value redacted).")


# --------------------------------------------------------------------------- dependency mapping
DEP_RX = [
    ("http", r"(requests|httpx|axios|fetch|RestTemplate|WebClient|urllib\.request)\b"),
    ("sdk", r"\b(stripe|razorpay|paypal|braintree|adyen|twilio|sendgrid|boto3|firebase)\b"),
    ("database", r"(session\.|\bdb\.|cursor\.|\.objects\.|execute\(|repository\.|Repository|mongoose|prisma\.|sequelize|knex|collection\()"),
    ("cache", r"\bredis\w*\b"),
    ("queue", r"(\.delay\(|apply_async|kafka|sqs|rabbit|amqp|bull\b|\.publish\()"),
]


def map_dependencies(ctx: Ctx) -> None:
    seen: set = set()
    for ep in ctx.endpoints:
        if ep["source"] != "code":
            continue
        for kind, rx in DEP_RX:
            h = find(ep["_segs"], rx)
            if not h:
                continue
            target = {"database": "database", "cache": "redis", "queue": "message queue"}.get(kind)
            if not target:
                m = re.search(rx, h[2], re.I)
                url = re.search(r"https?://([^/\"'`\s]+)", h[2])
                target = (url.group(1) if url else (m.group(1) if kind == "sdk" else "external HTTP (dynamic)")) if m else kind
            key = (ep["id"], kind, target)
            if key not in seen:
                seen.add(key)
                ctx.deps.append({"endpoint_id": ep["id"], "endpoint": f"{ep['method']} {ep['path']}", "kind": kind, "target": target, "file": h[0], "line": h[1]})
        for _, _, text in ep["_segs"][1:]:
            fn = re.search(r"def\s+(\w+)", text)
            if fn:
                ctx.deps.append({"endpoint_id": ep["id"], "endpoint": f"{ep['method']} {ep['path']}", "kind": "internal", "target": fn.group(1), "file": ep["file"], "line": ep["line"]})


# --------------------------------------------------------------------------- scan orchestration
class ScanRequest(BaseModel):
    repo_path: Optional[str] = None
    openapi_text: Optional[str] = None
    openapi_name: Optional[str] = "openapi-import"
    label: Optional[str] = None


def run_scan(req: ScanRequest) -> dict:
    if not req.repo_path and not req.openapi_text:
        raise HTTPException(400, "Provide repo_path and/or openapi_text")
    scan_id = uid("scan")
    ctx = Ctx(scan_id)
    target = req.repo_path or (req.openapi_name or "openapi")
    root = safe_root(req.repo_path) if req.repo_path else None
    audit("scan.start", scan_id=scan_id, target=str(root or target))
    run("INSERT INTO scans(id,created_at,target,kind,status,data) VALUES(?,?,?,?,?,?)",
        (scan_id, now(), str(root or target), "repo" if root else "openapi", "running", "{}"))
    try:
        if root:
            ctx.srcs = load_repo(root)
            pass1(ctx)
            for s in ctx.srcs:
                if s.ext == ".py":
                    py_endpoints(s, ctx)
                    drf_endpoints(s, ctx)
                elif s.ext in (".js", ".ts", ".jsx", ".tsx"):
                    js_endpoints(s, ctx)
                elif s.ext in (".java", ".kt"):
                    spring_endpoints(s, ctx)
                elif s.ext in (".json", ".yaml", ".yml") and re.search(r"^\s*[\"']?(openapi|swagger)[\"']?\s*:", s.text, re.M):
                    try:
                        analyze_openapi(ctx, s.text, s.rel)
                    except HTTPException:
                        pass
        if req.openapi_text:
            analyze_openapi(ctx, req.openapi_text, req.openapi_name or "openapi-import")
        seen = set()
        uniq = []
        for ep in ctx.endpoints:  # de-duplicate, then assign ids
            k = (ep["method"], ep["path"], ep["file"], ep.get("handler"))
            if k not in seen:
                seen.add(k)
                uniq.append(ep)
        ctx.endpoints = uniq
        for n, ep in enumerate(ctx.endpoints, 1):
            ep["id"] = f"EP-{n:03d}"
            ep["ref"] = f"{ep['method']} {ep['path']}"
        analyze_webhooks(ctx)
        analyze_payments(ctx)
        analyze_retries(ctx)
        analyze_security(ctx)
        map_dependencies(ctx)
        by_ep: dict[str, list] = {}
        for f in ctx.findings:
            by_ep.setdefault(f["endpoint_id"], []).append(f)
        for ep in ctx.endpoints:
            fs = by_ep.get(ep["id"], [])
            ep["health"] = max(0, round(100 - sum(SEV_W[f["severity"]] * CONF_W[f["confidence"]] for f in fs)))
            ep["finding_ids"] = [f["id"] for f in fs]
            ep.setdefault("features", {})
            segs = ep.pop("_segs", [])
            ep["segments"] = [(a, b) for a, b, _ in segs][:6]
            save_item(f"{scan_id}:{ep['id']}", scan_id, "endpoint", ep)
        for f in ctx.findings:
            save_item(f["id"], scan_id, "finding", f)
        for typ, rows in (("webhook", ctx.webhooks), ("dependency", ctx.deps), ("retry", ctx.retries), ("idempotency", ctx.idem), ("spec", ctx.specs)):
            for n, r in enumerate(rows):
                save_item(f"{scan_id}:{typ}:{n}", scan_id, typ, r)
        sev: dict[str, int] = {}
        cat: dict[str, int] = {}
        for f in ctx.findings:
            sev[f["severity"]] = sev.get(f["severity"], 0) + 1
            cat[f["category"]] = cat.get(f["category"], 0) + 1
        summary = {"endpoints": len(ctx.endpoints), "webhooks": len(ctx.webhooks), "findings": len(ctx.findings), "by_severity": sev, "by_category": cat,
                   "retry_configs": len(ctx.retries), "files_scanned": len(ctx.srcs), "specs": ctx.specs, "label": req.label,
                   "evidence_sources": {"static_analysis": len(ctx.findings), "simulated": 0, "operational_telemetry": 0},
                   "notes": ["Health scores derive from static findings only; no runtime telemetry is connected.", "Repository analysis was read-only."]}
        run("UPDATE scans SET status='completed', data=? WHERE id=?", (json.dumps(summary), scan_id))
        audit("scan.complete", scan_id=scan_id, findings=len(ctx.findings))
        return {"id": scan_id, "status": "completed", "summary": summary}
    except HTTPException:
        run("UPDATE scans SET status='failed' WHERE id=?", (scan_id,))
        raise
    except Exception as e:  # noqa: BLE001
        run("UPDATE scans SET status='failed', data=? WHERE id=?", (json.dumps({"error": str(e)}), scan_id))
        audit("scan.error", scan_id=scan_id, error=str(e))
        raise HTTPException(500, f"Scan failed: {e}")


# --------------------------------------------------------------------------- failure simulation engine (in-process, no network)
class Profile(BaseModel):
    verifies_signature: bool = True
    checks_timestamp: bool = True
    tolerance_s: int = 300
    dedupes_events: bool = True
    dedupe_atomic: bool = True
    orders_events: bool = True
    uses_idempotency_keys: bool = True
    validates_input: bool = True
    persists_events: bool = True
    swallows_errors: bool = False
    checks_provider_response: bool = True
    max_retries: int = 3
    backoff: str = "exponential"  # none | fixed | exponential
    base_delay_s: float = 0.5
    jitter: bool = True
    retry_non_retryable: bool = False
    timeout_s: float = 5.0


NAIVE = Profile(verifies_signature=False, checks_timestamp=False, dedupes_events=False, dedupe_atomic=False, orders_events=False,
                uses_idempotency_keys=False, validates_input=False, persists_events=False, checks_provider_response=False,
                max_retries=5, backoff="none", jitter=False, retry_non_retryable=True)


def profile_from_features(f: dict) -> Profile:
    p = Profile()
    if f.get("webhook"):
        p.verifies_signature = bool(f.get("verify"))
        p.checks_timestamp = bool(f.get("timestamp"))
        p.dedupes_events = bool(f.get("dedupe"))
        p.dedupe_atomic = bool(f.get("dedupe_atomic", True))
        p.orders_events = bool(f.get("orders_events", True))
        p.persists_events = bool(f.get("persist"))
        p.swallows_errors = bool(f.get("swallows_errors"))
        p.timeout_s = 2.0 if (f.get("heavy") and not f.get("async")) else p.timeout_s
    if f.get("payment"):
        p.uses_idempotency_keys = bool(f.get("idem") or f.get("idempotency_key"))
        p.checks_provider_response = bool(f.get("resp_check"))
        p.orders_events = bool(f.get("transition_guard"))
        p.dedupe_atomic = bool(f.get("lock"))
        p.persists_events = bool(f.get("pending"))
    p.validates_input = f.get("validates", p.validates_input)
    return p


SCENARIOS = {
    "http_status": {"desc": "Client handling of 400/401/403/404/409/429/500 responses", "params": {"status": 500, "recover_after": None}},
    "network_timeout": {"desc": "Provider processes the charge but the response times out (ambiguous outcome)", "params": {"fail_count": 1}},
    "connection_interruption": {"desc": "Connection reset before/after the request is sent", "params": {"phase": "after_send"}},
    "delayed_response": {"desc": "Provider latency vs client timeout", "params": {"latency_ms": 8000}},
    "duplicate_webhook": {"desc": "Same webhook event delivered repeatedly (optionally concurrently)", "params": {"copies": 3, "concurrent": False}},
    "invalid_signature": {"desc": "Forged/tampered webhook signature", "params": {}},
    "expired_timestamp": {"desc": "Correctly signed but stale (replayed) webhook", "params": {"age_s": 3600}},
    "out_of_order": {"desc": "authorized(1) -> captured(2) -> refunded(3) delivered out of order", "params": {"order": [3, 1, 2]}},
    "retry_exhaustion": {"desc": "Provider fails permanently; are retries bounded and is the failure recorded?", "params": {"max_acceptable_attempts": 10}},
    "provider_unavailable": {"desc": "Provider outage window with many clients (thundering herd)", "params": {"outage_s": 30, "clients": 20}},
    "malformed_payload": {"desc": "Malformed inbound request / provider response", "params": {"kind": "both"}},
}
EPOCH = 1_700_000_000
SECRET = b"whsec_synthetic_simulation_key"


class Sim:
    def __init__(self, p: Profile, seed: int, params: dict):
        self.p, self.rng, self.params, self.t = p, random.Random(seed), params, 0.0
        self.trace: list[dict] = []
        self.asserts: list[dict] = []
        self.charges: list[str] = []
        self.idem: dict[str, str] = {}

    def log(self, event: str, **kw: Any) -> None:
        self.trace.append({"t": round(self.t, 3), "event": event, **kw})

    def check(self, name: str, expected: Any, actual: Any) -> None:
        self.asserts.append({"name": name, "expected": expected, "actual": actual, "passed": expected == actual})

    def provider_charge(self, key: Optional[str]) -> str:
        if key and key in self.idem:
            self.log("provider.dedup", key=key, charge=self.idem[key])
            return self.idem[key]
        cid = f"ch_{len(self.charges) + 1:03d}"
        self.charges.append(cid)
        if key:
            self.idem[key] = cid
        self.log("provider.charge_created", charge=cid, key=key)
        return cid

    def delay(self, attempt: int) -> float:
        p = self.p
        d = 0.0 if p.backoff == "none" else p.base_delay_s if p.backoff == "fixed" else min(60.0, p.base_delay_s * 2 ** (attempt - 1))
        return round(self.rng.uniform(0, d), 3) if (p.jitter and d) else d

    def client(self, fault: Callable[[int], dict]) -> dict:
        p = self.p
        key = f"idem-{self.rng.randrange(10 ** 8):08d}" if p.uses_idempotency_keys else None
        attempts, outcome = 0, "unknown"
        while True:
            attempts += 1
            f = fault(attempts)
            typ = f["type"]
            self.log("client.request", attempt=attempts, key=key, fault=typ, code=f.get("code"))
            if typ == "ok" or f.get("processed"):
                self.provider_charge(key)
            if typ == "ok":
                outcome = "success"
                break
            retryable = f.get("code") in (429, 500, 502, 503, 504) or typ in ("timeout", "reset", "malformed")
            if typ == "status" and f["code"] == 409 and key:
                outcome = "conflict_treated_as_duplicate"
                break
            if typ == "status" and not retryable and not p.retry_non_retryable:
                outcome = f"failed_{f['code']}"
                break
            if typ == "malformed" and not p.checks_provider_response:
                outcome = "marked_paid_on_malformed_response"
                break
            if attempts > p.max_retries or attempts > 51:
                outcome = "retries_exhausted"
                break
            d = self.delay(attempts)
            self.t += d
            self.log("client.backoff", seconds=d)
        return {"attempts": attempts, "outcome": outcome, "key": key}


def sign(ts: int, body: bytes, secret: bytes = SECRET) -> str:
    return hmac.new(secret, f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


class Receiver:
    def __init__(self, sim: Sim):
        self.s, self.seen, self.applied, self.credit, self.status, self.last_seq, self.stored = sim, set(), [], 0, "none", 0, []

    def check_headers(self, body: bytes, ts: int, sig: str) -> Optional[str]:
        p, s = self.s.p, self.s
        if p.verifies_signature and not hmac.compare_digest(sig, sign(ts, body)):
            return "rejected_bad_signature"
        if p.checks_timestamp and abs(EPOCH + s.t - ts) > p.tolerance_s:
            return "rejected_stale_timestamp"
        return None

    def apply(self, ev: dict) -> None:
        self.credit += ev.get("amount", 0)
        self.applied.append(ev["id"])
        self.status = ev.get("status", self.status)
        self.s.log("receiver.applied", event=ev["id"], credit=self.credit, status=self.status)

    def deliver(self, ev: dict, ts: Optional[int] = None, sig: Optional[str] = None) -> str:
        body = json.dumps(ev, sort_keys=True).encode()
        ts = ts if ts is not None else int(EPOCH + self.s.t)
        sig = sig if sig is not None else sign(ts, body)
        rej = self.check_headers(body, ts, sig)
        if rej:
            self.s.log("receiver.rejected", event=ev["id"], reason=rej)
            return rej
        if self.s.p.persists_events:
            self.stored.append(ev["id"])
        if self.s.p.dedupes_events:
            if ev["id"] in self.seen:
                self.s.log("receiver.duplicate_ignored", event=ev["id"])
                return "duplicate_ignored"
            self.seen.add(ev["id"])
        if self.s.p.orders_events and ev.get("seq"):
            if ev["seq"] <= self.last_seq:
                self.s.log("receiver.stale_ignored", event=ev["id"], seq=ev["seq"], last=self.last_seq)
                return "stale_ignored"
            self.last_seq = ev["seq"]
        self.apply(ev)
        return "applied"

    def deliver_concurrent(self, evs: list[dict]) -> list[str]:
        p = self.s.p
        if not (p.dedupes_events and not p.dedupe_atomic):
            return [self.deliver(e) for e in evs]
        passed = [e for e in evs if e["id"] not in self.seen]  # all workers read before anyone writes
        self.s.log("receiver.race", workers=len(evs), passed_check=len(passed))
        for e in passed:
            self.seen.add(e["id"])
            self.apply(e)
        return ["applied" if e in passed else "duplicate_ignored" for e in evs]


def clamp(v: Any, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def scenario_run(name: str, s: Sim) -> dict:
    p, prm = s.p, s.params
    if name == "http_status":
        code = clamp(prm.get("status"), 400, 599, 500)
        ra = prm.get("recover_after")
        ra = clamp(ra, 0, 20, 2) if ra is not None else (2 if code in (429, 500) else 0)
        r = s.client(lambda a: {"type": "ok"} if (ra and a > ra) else {"type": "status", "code": code, "processed": False})
        if code in (400, 401, 403, 404, 409):
            s.check(f"HTTP {code} is not retried", 1, r["attempts"])
        else:
            s.check(f"HTTP {code} recovers after retries without duplicate charge", 1, len(s.charges))
            s.check("retries are bounded", True, r["attempts"] <= p.max_retries + 1)
        return {"result": r, "charges": len(s.charges)}
    if name in ("network_timeout", "connection_interruption", "delayed_response"):
        if name == "network_timeout":
            n = clamp(prm.get("fail_count"), 1, 10, 1)
            fault = lambda a: {"type": "timeout", "processed": True} if a <= n else {"type": "ok"}
        elif name == "connection_interruption":
            after = prm.get("phase", "after_send") == "after_send"
            fault = lambda a: {"type": "reset", "processed": after} if a == 1 else {"type": "ok"}
        else:
            lat = clamp(prm.get("latency_ms"), 0, 600000, 8000) / 1000
            fault = (lambda a: {"type": "timeout", "processed": True} if (a == 1 and lat >= p.timeout_s) else {"type": "ok"})
            s.log("delay.config", latency_s=lat, timeout_s=p.timeout_s)
        r = s.client(fault)
        s.check("exactly one provider effect (at-least-once delivery + idempotent processing)", 1, len(s.charges))
        s.check("client ends in success", "success", r["outcome"])
        return {"result": r, "charges": len(s.charges)}
    rcv = Receiver(s)
    if name == "duplicate_webhook":
        n = clamp(prm.get("copies"), 2, 200, 3)
        evs = [{"id": "evt_001", "type": "payment.succeeded", "amount": 500, "seq": 1, "status": "captured"}] * n
        res = rcv.deliver_concurrent(list(evs)) if prm.get("concurrent") else [rcv.deliver(e) for e in evs]
        s.check("side effect applied exactly once", 500, rcv.credit)
        return {"credit": rcv.credit, "responses": res}
    if name == "invalid_signature":
        ev = {"id": "evt_forged", "amount": 999, "type": "payment.succeeded", "seq": 1}
        r = [rcv.deliver(ev, sig="0" * 64), rcv.deliver({**ev, "id": "evt_forged2"}, sig=sign(int(EPOCH), b"x", b"wrong-secret"))]
        s.check("forged events are rejected", ["rejected_bad_signature"] * 2, r)
        s.check("no side effects from forged events", 0, rcv.credit)
        return {"credit": rcv.credit, "responses": r}
    if name == "expired_timestamp":
        ev = {"id": "evt_old", "amount": 700, "seq": 1}
        old = int(EPOCH - clamp(prm.get("age_s"), 1, 10 ** 8, 3600))
        body = json.dumps(ev, sort_keys=True).encode()
        r = rcv.deliver(ev, ts=old, sig=sign(old, body))
        s.check("stale (replayed) event is rejected", "rejected_stale_timestamp", r)
        return {"response": r, "credit": rcv.credit}
    if name == "out_of_order":
        evs = {1: {"id": "e1", "seq": 1, "status": "authorized"}, 2: {"id": "e2", "seq": 2, "status": "captured"}, 3: {"id": "e3", "seq": 3, "status": "refunded"}}
        order = [o for o in (prm.get("order") or [3, 1, 2]) if o in evs] or [3, 1, 2]
        for o in order:
            rcv.deliver(evs[o])
        s.check("final state follows event sequence, not arrival order", "refunded", rcv.status)
        return {"delivery_order": order, "final_status": rcv.status}
    if name == "retry_exhaustion":
        r = s.client(lambda a: {"type": "status", "code": 503, "processed": False})
        limit = clamp(prm.get("max_acceptable_attempts"), 1, 100, 10)
        s.check(f"attempts bounded (<= {limit})", True, r["attempts"] <= limit)
        s.check("exhausted work is recorded for replay (dead-letter), not dropped", True, bool(p.persists_events) and not p.swallows_errors)
        s.log("client.final", outcome="dead_lettered" if p.persists_events and not p.swallows_errors else "lost")
        return {"result": r}
    if name == "provider_unavailable":
        outage, clients = clamp(prm.get("outage_s"), 1, 3600, 30), clamp(prm.get("clients"), 2, 500, 20)
        buckets: dict[int, int] = {}
        recovered = 0
        for c in range(clients):
            sub = Sim(p, s.rng.randrange(10 ** 9), {})
            t, ok = 0.0, False
            for a in range(1, min(p.max_retries, 50) + 2):
                buckets[int(t * 10)] = buckets.get(int(t * 10), 0) + (1 if a > 1 else 0)
                if t >= outage:
                    ok = True
                    break
                t += sub.delay(a)
            recovered += ok
        peak = max(buckets.values()) if buckets else 0
        s.log("herd", clients=clients, recovered=recovered, peak_synchronised_retries=peak)
        s.check("all clients recover after the outage within their retry budget", clients, recovered)
        s.check("retries are spread (no synchronised herd > 50% of clients in one 100ms bucket)", True, peak <= clients * 0.5)
        return {"clients": clients, "recovered": recovered, "peak_synchronised_retries": peak}
    if name == "malformed_payload":
        kind, out = prm.get("kind", "both"), {}
        if kind in ("request", "both"):
            status = 400 if p.validates_input else 500
            s.log("receiver.malformed_request", handled_status=status)
            s.check("malformed request is rejected with 4xx (not 5xx/accepted)", True, 400 <= status < 500)
            out["request_status"] = status
        if kind in ("response", "both"):
            r = s.client(lambda a: {"type": "malformed", "processed": True} if a == 1 else {"type": "ok"}) if p.checks_provider_response else \
                {"outcome": "marked_paid_on_malformed_response", "attempts": 1}
            if not p.checks_provider_response:
                s.log("client.malformed_response_accepted")
            s.check("malformed provider response never marks the payment as paid", True, r["outcome"] != "marked_paid_on_malformed_response")
            out["response_outcome"] = r["outcome"]
        return out
    raise HTTPException(400, f"Unknown scenario '{name}'. Available: {sorted(SCENARIOS)}")


def run_simulation(scenario: str, profile: Profile, seed: int, params: dict, meta: Optional[dict] = None) -> dict:
    prm = {**{k: v for k, v in (SCENARIOS.get(scenario, {}).get("params") or {}).items() if v is not None}, **(params or {})}
    s = Sim(profile, seed, prm)
    outcome = scenario_run(scenario, s)
    if len(s.trace) > 5000:
        s.trace = s.trace[:5000]
    passed = all(a["passed"] for a in s.asserts)
    core = {"scenario": scenario, "seed": seed, "params": prm, "profile": profile.model_dump(), "trace": s.trace, "assertions": s.asserts}
    return {"scenario": scenario, "seed": seed, "params": prm, "profile": profile.model_dump(), "verdict": "pass" if passed else "fail",
            "outcome": outcome, "assertions": s.asserts, "trace": s.trace, "trace_hash": hashlib.sha256(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest(),
            "evidence_type": "simulated", "network": "none (in-process virtual provider; no sockets opened)", "synthetic_payloads": True, **(meta or {})}


class SimRequest(BaseModel):
    scenario: Optional[str] = None
    target: str = "reference"  # reference | naive | scan:<scan_id>:<endpoint_id>
    seed: int = 1337
    params: dict = Field(default_factory=dict)
    profile: dict = Field(default_factory=dict)
    finding_id: Optional[str] = None


def resolve_target(target: str) -> tuple[Profile, dict]:
    if target == "reference":
        return Profile(), {"target": "reference"}
    if target == "naive":
        return NAIVE.model_copy(), {"target": "naive"}
    m = re.fullmatch(r"scan:([\w-]+):(EP-\d+)", target)
    if not m:
        raise HTTPException(400, "target must be 'reference', 'naive' or 'scan:<scan_id>:<endpoint_id>'")
    rows = run("SELECT data FROM items WHERE id=?", (f"{m.group(1)}:{m.group(2)}",))
    if not rows:
        raise HTTPException(404, "Endpoint not found for target")
    ep = json.loads(rows[0]["data"])
    return profile_from_features(ep.get("features", {})), {"target": target, "endpoint": ep["ref"], "profile_source": "static features of scanned endpoint"}


def simulate(req: SimRequest) -> dict:
    scenario, target, params, over = req.scenario, req.target, dict(req.params), dict(req.profile)
    if req.finding_id:
        rows = run("SELECT data FROM items WHERE id=?", (req.finding_id,))
        if not rows:
            raise HTTPException(404, "Finding not found")
        sim = json.loads(rows[0]["data"]).get("sim")
        if not sim:
            raise HTTPException(400, "This finding has no linked simulation scenario")
        scenario = scenario or sim["scenario"]
        target = sim["target"] if req.target == "reference" else target
        params = {**(sim.get("params") or {}), **params}
        over = {**(sim.get("profile") or {}), **over}
    if not scenario:
        raise HTTPException(400, "scenario is required")
    if scenario not in SCENARIOS:
        raise HTTPException(400, f"Unknown scenario. Available: {sorted(SCENARIOS)}")
    prof, meta = resolve_target(target)
    try:
        prof = Profile(**{**prof.model_dump(), **over})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Invalid profile override: {e}")
    prof.max_retries = clamp(prof.max_retries, 0, 50, 3)
    res = run_simulation(scenario, prof, req.seed, params, {**meta, "finding_id": req.finding_id})
    rid = uid("sim")
    res.update({"id": rid, "created_at": now()})
    run("INSERT INTO simulations(id,created_at,data) VALUES(?,?,?)", (rid, res["created_at"], json.dumps(res, default=str)))
    audit("simulation.run", run_id=rid, scenario=scenario, target=target, verdict=res["verdict"])
    return res


# --------------------------------------------------------------------------- remediation / integration
def create_plan(finding: dict) -> dict:
    sim = finding.get("sim")
    plan = {
        "finding_id": finding["id"], "title": f"Remediate: {finding['title']}", "severity": finding["severity"], "confidence": finding["confidence"],
        "risk": {"severity": finding["severity"], "blast_radius": finding["endpoint"], "needs_verification": finding["needs_verification"]},
        "steps": [{"n": 1, "action": "Confirm the finding", "detail": f"Review {finding['evidence']['location']}" + (" (absence-based: verify gateway/framework behaviour first)" if finding["needs_verification"] else "")},
                  {"n": 2, "action": "Reproduce safely", "detail": finding["reproduction"]},
                  {"n": 3, "action": "Implement fix", "detail": finding["remediation"]},
                  {"n": 4, "action": "Add regression tests", "detail": "; ".join(finding["tests"])},
                  {"n": 5, "action": "Review & approve", "detail": "Patch is prepared in an isolated branch only; a human must approve before any merge/deploy."}],
        "test_requirements": finding["tests"],
        "simulation_hint": sim,
        "module_handoffs": {"module02": "plan", "module03": "prepare isolated patch (approval required)", "module05": "validate with listed tests",
                            "module06": sim if finding["category"] == "payment" else None},
        "status": "draft_pending_approval", "auto_apply": False, "created_at": now(),
    }
    for hook, key in (("module02.create_plan", "module02"), ("module03.prepare_patch", "module03"), ("module05.validate", "module05"), ("module06.scenario", "module06")):
        fn = INTEGRATIONS.get(hook)
        if fn and (key != "module06" or finding["category"] == "payment"):
            try:
                plan.setdefault("integration_results", {})[key] = fn(plan if key != "module06" else (sim or {}))
            except Exception as e:  # noqa: BLE001
                plan.setdefault("integration_results", {})[key] = {"error": str(e)}
        else:
            plan.setdefault("integration_results", {})[key] = "no module registered - plan stored locally (INTEGRATIONS['%s'])" % hook
    pid = uid("plan")
    plan["id"] = pid
    run("INSERT INTO plans(id,finding_id,created_at,data) VALUES(?,?,?,?)", (pid, finding["id"], plan["created_at"], json.dumps(plan, default=str)))
    audit("plan.create", plan_id=pid, finding_id=finding["id"])
    return plan


def compare_scans(base: str, head: str) -> dict:
    def eps(sid):
        return {e["ref"]: e for e in load_items(sid, "endpoint")}
    b, h = eps(base), eps(head)
    bf = {f["fingerprint"]: f for f in load_items(base, "finding")}
    hf = {f["fingerprint"]: f for f in load_items(head, "finding")}
    breaking = []
    for ref, e in b.items():
        if ref not in h:
            breaking.append({"type": "endpoint_removed", "endpoint": ref, "confidence": "high"})
            continue
        c1, c2 = e.get("contract"), h[ref].get("contract")
        if c1 and c2:
            for x in set(c2["required_params"]) - set(c1["required_params"]):
                breaking.append({"type": "new_required_param", "endpoint": ref, "detail": x, "confidence": "high"})
            for x in set(c2["required_body"]) - set(c1["required_body"]):
                breaking.append({"type": "new_required_body_field", "endpoint": ref, "detail": x, "confidence": "high"})
            for x in set(c1["response_codes"]) - set(c2["response_codes"]):
                if x.startswith("2"):
                    breaking.append({"type": "success_response_removed", "endpoint": ref, "detail": x, "confidence": "high"})
            for k, v in c1["body_props"].items():
                if k in c2["body_props"] and c2["body_props"][k] != v:
                    breaking.append({"type": "field_type_changed", "endpoint": ref, "detail": f"{k}: {v}->{c2['body_props'][k]}", "confidence": "high"})
        elif e["auth"] != h[ref]["auth"]:
            breaking.append({"type": "auth_changed", "endpoint": ref, "detail": f"{e['auth']}->{h[ref]['auth']}", "confidence": "medium"})
    return {"base": base, "head": head, "endpoints_added": sorted(set(h) - set(b)), "endpoints_removed": sorted(set(b) - set(h)),
            "breaking_changes": breaking, "findings_new": [hf[k] for k in hf.keys() - bf.keys()], "findings_resolved": [bf[k] for k in bf.keys() - hf.keys()],
            "findings_persisting": len(hf.keys() & bf.keys()), "note": "Breaking changes are reported only where both scans carry contract evidence (OpenAPI) or the endpoint disappeared."}


# --------------------------------------------------------------------------- API
router = APIRouter(prefix="/api/v1/api-guardian", tags=["api-guardian"])


def scan_or_latest(scan_id: Optional[str]) -> str:
    sid = scan_id or latest_scan_id()
    if not sid:
        raise HTTPException(404, "No completed scans yet - run POST /scan first")
    return sid


@router.post("/scan")
def api_scan(req: ScanRequest):
    return run_scan(req)


@router.post("/openapi")
def api_openapi(req: ScanRequest):
    if not req.openapi_text:
        raise HTTPException(400, "openapi_text is required")
    return run_scan(ScanRequest(openapi_text=req.openapi_text, openapi_name=req.openapi_name, label=req.label))


@router.get("/scans")
def api_scans():
    return [{"id": r["id"], "created_at": r["created_at"], "target": r["target"], "kind": r["kind"], "status": r["status"], "summary": json.loads(r["data"] or "{}")}
            for r in run("SELECT * FROM scans ORDER BY created_at DESC, rowid DESC LIMIT 100")]


@router.get("/scans/{scan_id}")
def api_scan_get(scan_id: str):
    r = run("SELECT * FROM scans WHERE id=?", (scan_id,))
    if not r:
        raise HTTPException(404, "Scan not found")
    r = r[0]
    return {"id": r["id"], "created_at": r["created_at"], "target": r["target"], "kind": r["kind"], "status": r["status"], "summary": json.loads(r["data"] or "{}"),
            "retry_configs": load_items(scan_id, "retry"), "idempotency": load_items(scan_id, "idempotency")}


@router.get("/endpoints")
def api_endpoints(scan_id: Optional[str] = None, q: Optional[str] = None):
    sid = scan_or_latest(scan_id)
    rows = load_items(sid, "endpoint")
    if q:
        rows = [r for r in rows if q.lower() in r["ref"].lower() or q.lower() in r["file"].lower()]
    return {"scan_id": sid, "evidence_type": "static_analysis", "items": rows}


@router.get("/webhooks")
def api_webhooks(scan_id: Optional[str] = None):
    sid = scan_or_latest(scan_id)
    return {"scan_id": sid, "evidence_type": "static_analysis", "items": load_items(sid, "webhook")}


@router.get("/dependencies")
def api_deps(scan_id: Optional[str] = None):
    sid = scan_or_latest(scan_id)
    return {"scan_id": sid, "evidence_type": "static_analysis", "items": load_items(sid, "dependency")}


@router.get("/findings")
def api_findings(scan_id: Optional[str] = None, category: Optional[str] = None, severity: Optional[str] = None, endpoint_id: Optional[str] = None):
    sid = scan_or_latest(scan_id)
    rows = load_items(sid, "finding")
    if category:
        rows = [r for r in rows if r["category"] in category.split(",")]
    if severity:
        rows = [r for r in rows if r["severity"] == severity]
    if endpoint_id:
        rows = [r for r in rows if r["endpoint_id"] == endpoint_id]
    rows.sort(key=lambda r: (-SEV_ORDER.index(r["severity"]), r["id"]))
    return {"scan_id": sid, "items": rows}


@router.get("/findings/{finding_id}")
def api_finding(finding_id: str):
    r = run("SELECT data FROM items WHERE id=? AND type='finding'", (finding_id,))
    if not r:
        raise HTTPException(404, "Finding not found")
    f = json.loads(r[0]["data"])
    f["linked_simulations"] = [json.loads(x["data"]) | {"trace": "omitted"} for x in run("SELECT data FROM simulations ORDER BY created_at DESC LIMIT 200")
                               if f["id"] == json.loads(x["data"]).get("finding_id")][:10]
    f["plans"] = [json.loads(x["data"]) for x in run("SELECT data FROM plans WHERE finding_id=? ORDER BY created_at DESC", (finding_id,))]
    return f


@router.get("/scenarios")
def api_scenarios():
    return SCENARIOS


@router.post("/simulations")
def api_sim(req: SimRequest):
    return simulate(req)


@router.get("/simulations")
def api_sims():
    return [{k: v for k, v in json.loads(r["data"]).items() if k != "trace"} for r in run("SELECT data FROM simulations ORDER BY created_at DESC LIMIT 100")]


@router.get("/simulations/{run_id}")
def api_sim_get(run_id: str):
    r = run("SELECT data FROM simulations WHERE id=?", (run_id,))
    if not r:
        raise HTTPException(404, "Simulation run not found")
    return json.loads(r[0]["data"])


@router.post("/findings/{finding_id}/create-plan")
def api_plan(finding_id: str):
    r = run("SELECT data FROM items WHERE id=? AND type='finding'", (finding_id,))
    if not r:
        raise HTTPException(404, "Finding not found")
    return create_plan(json.loads(r[0]["data"]))


@router.get("/compare")
def api_compare(base: str, head: str):
    for s in (base, head):
        if not run("SELECT id FROM scans WHERE id=?", (s,)):
            raise HTTPException(404, f"Scan {s} not found")
    return compare_scans(base, head)


@router.get("/audit")
def api_audit(limit: int = Query(100, le=500)):
    return run("SELECT ts,action,detail FROM audit ORDER BY id DESC LIMIT ?", (limit,))


@router.get("/ui", response_class=HTMLResponse)
def api_ui():
    p = Path(__file__).with_name("guardian_ui.html")
    return HTMLResponse(p.read_text(encoding="utf-8") if p.exists() else "<h1>guardian_ui.html not found</h1>")


def create_app() -> FastAPI:
    app = FastAPI(title="FORGEX API Guardian")
    app.include_router(router)
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8007)
