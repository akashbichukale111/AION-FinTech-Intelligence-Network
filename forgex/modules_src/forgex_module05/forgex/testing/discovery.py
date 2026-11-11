"""A. Automated test discovery (Python, Java, JavaScript/TypeScript)."""
from __future__ import annotations
import ast, json, os, re
from collections import Counter
from pathlib import Path

IGNORE = {".git", "node_modules", "__pycache__", ".venv", "venv", "env", "dist", "build", "target", ".tox",
          ".mypy_cache", ".pytest_cache", "coverage", ".gradle", ".idea"}
LANG = {".py": "python", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
        ".ts": "typescript", ".tsx": "typescript", ".java": "java"}
PY_TEST = re.compile(r"^(test_.*|.*_test)\.py$")
JS_TEST = re.compile(r".*\.(test|spec)\.(js|jsx|ts|tsx|mjs|cjs)$")
JAVA_TEST = re.compile(r"^(.*Tests?|Test.*)\.java$")
CRITICAL = re.compile(r"payment|refund|ledger|transaction|webhook|settle|idempot|auth|billing|invoice|money", re.I)


def iter_files(root, cap=30000):
    n = 0
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in IGNORE and not d.endswith(".egg-info")]
        for f in fn:
            yield (Path(dp) / f).relative_to(root).as_posix()
            n += 1
            if n >= cap:
                return

def read(root, rel, limit=500_000):
    try:
        with open(Path(root) / rel, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""

def lang_of(rel):
    return LANG.get(Path(rel).suffix)

def is_test_file(rel):
    name, lang = Path(rel).name, lang_of(rel)
    if lang == "python": return bool(PY_TEST.match(name))
    if lang in ("javascript", "typescript"): return bool(JS_TEST.match(name)) or "/__tests__/" in "/" + rel
    if lang == "java": return "src/test/" in rel and bool(JAVA_TEST.match(name))
    return False

def is_source_file(rel):
    lang = lang_of(rel)
    if not lang or is_test_file(rel): return False
    n = Path(rel).name
    if n in ("conftest.py", "setup.py") or n.endswith((".config.js", ".config.ts", ".d.ts")): return False
    if lang == "java": return "src/main/" in rel
    return not re.search(r"(^|/)(tests?|__tests__|e2e|docs?|examples?|scripts)/", rel)

# ---------- import resolution ----------
def py_imports(rel, text):
    out = []
    try: tree = ast.parse(text)
    except SyntaxError: return out
    pkg = rel.split("/")[:-1]
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            base = n.module or ""
            if n.level:
                parts = pkg[:max(0, len(pkg) - (n.level - 1))]
                base = ".".join(parts + ([n.module] if n.module else []))
            if base: out.append(base)
            out += [f"{base}.{a.name}" if base else a.name for a in n.names]
    return out

def py_module_map(files):
    m = {}
    for f in files:
        if not f.endswith(".py"): continue
        parts = f[:-3].split("/")
        if parts[-1] == "__init__": parts = parts[:-1]
        if not parts: continue
        m.setdefault(".".join(parts), f)
        if parts[0] in ("src", "lib") and len(parts) > 1:
            m.setdefault(".".join(parts[1:]), f)
    return m

JS_IMP = re.compile(r"""(?:from\s*|require\(\s*|import\(\s*|import\s+)['"](\.{1,2}/[^'"]*)['"]""")
def js_resolve(rel, spec, fileset):
    base = os.path.normpath(os.path.join(os.path.dirname(rel), spec)).replace("\\", "/")
    for cand in [base] + [base + e for e in (".ts", ".tsx", ".js", ".jsx", ".mjs")] + \
                [f"{base}/index{e}" for e in (".ts", ".tsx", ".js", ".jsx")]:
        if cand in fileset: return cand
    return None

JAVA_IMP = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+?)(?:\.\*)?;", re.M)

def build_import_graph(root, files):
    fileset, graph = set(files), {}
    pymap = py_module_map(files)
    java_by_fqn = {}
    for f in files:
        if f.endswith(".java") and "src/" in f:
            java_by_fqn[re.sub(r"^.*?src/(main|test)/java/", "", f)[:-5].replace("/", ".")] = f
    for f in files:
        lang = lang_of(f)
        if not lang: continue
        text = read(root, f)
        deps = set()
        if lang == "python":
            for name in py_imports(f, text):
                if name in pymap and pymap[name] != f: deps.add(pymap[name])
        elif lang in ("javascript", "typescript"):
            for spec in JS_IMP.findall(text):
                r = js_resolve(f, spec, fileset)
                if r and r != f: deps.add(r)
        elif lang == "java":
            for fqn in JAVA_IMP.findall(text):
                for cand in (fqn, fqn.rsplit(".", 1)[0]):
                    if cand in java_by_fqn and java_by_fqn[cand] != f: deps.add(java_by_fqn[cand])
        graph[f] = sorted(deps)
    return graph

# ---------- per-test-file analysis ----------
def analyze_test(lang, text):
    tests = asserts = skipped = 0
    if lang == "python":
        try: tree = ast.parse(text)
        except SyntaxError: return {"tests": 0, "assertions": 0, "skipped": 0, "syntax_error": True, "unittest": False}
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if n.name.startswith("test"): tests += 1
                if any("skip" in ast.unparse(d) or "xfail" in ast.unparse(d) for d in n.decorator_list): skipped += 1
            elif isinstance(n, ast.Assert): asserts += 1
            elif isinstance(n, ast.Call):
                f = n.func
                nm = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                if nm.startswith("assert") or nm in ("raises", "warns", "approx"): asserts += 1
        return {"tests": tests, "assertions": asserts, "skipped": skipped, "unittest": "unittest" in text and "TestCase" in text}
    if lang in ("javascript", "typescript"):
        tests = len(re.findall(r"\b(?:it|test)(?:\.each\([^)]*\))?\s*\(", text))
        skipped = len(re.findall(r"\b(?:it|test|describe)\.(?:skip|todo)\b|\bxit\(|\bxdescribe\(", text))
        asserts = len(re.findall(r"\bexpect\(|\bassert[.(]", text))
    else:
        tests = len(re.findall(r"@(?:Parameterized)?Test\b", text))
        skipped = len(re.findall(r"@(?:Disabled|Ignore)\b", text))
        asserts = len(re.findall(r"\bassert\w*\(|\bverify\(|\bassertThat\(", text))
    return {"tests": tests, "assertions": asserts, "skipped": skipped}

def classify_kind(rel, text):
    low = rel.lower()
    if re.search(r"integration|e2e|end_to_end|testcontainers|docker|\bpytest\.mark\.integration|SpringBootTest", rel + text[:4000], re.I):
        return "integration"
    if re.search(r"(^|/)(api|contract|routes?|controllers?)(/|_|\.)|TestClient|supertest|MockMvc|RestAssured|test_client\(|requests\.(get|post)", low + text[:6000]):
        return "api"
    return "unit"

# ---------- frameworks ----------
def detect_frameworks(root, files, test_files):
    fw = []
    fset = set(files)
    def has(name): return any(f == name or f.endswith("/" + name) for f in files)
    py_tests = [t for t in test_files if t["language"] == "python"]
    if py_tests or has("pytest.ini") or has("conftest.py"):
        ev, cfg = [], []
        blob = "".join(read(root, c) for c in ("pyproject.toml", "setup.cfg", "tox.ini", "requirements.txt", "requirements-dev.txt") if c in fset)
        if has("pytest.ini"): ev.append("pytest.ini"); cfg.append("pytest.ini")
        if has("conftest.py"): ev.append("conftest.py")
        if re.search(r"pytest", blob): ev.append("pytest referenced in project config")
        if any(read(root, t["path"], 20000).find("import pytest") >= 0 for t in py_tests[:50]): ev.append("test files import pytest")
        uses_unittest_only = py_tests and all(t.get("unittest") for t in py_tests)
        name = "pytest" if (ev or not uses_unittest_only) else "unittest"
        fw.append({"language": "python", "framework": name, "evidence": ev or ["test_*.py naming"], "config_files": cfg,
                   "suggested_command": "python -m pytest -q" if name == "pytest" else "python -m unittest discover -v"})
    if "package.json" in fset:
        try: pj = json.loads(read(root, "package.json"))
        except ValueError: pj = {}
        deps = {**pj.get("dependencies", {}), **pj.get("devDependencies", {})}
        for lib, cmd in (("jest", "npx --no-install jest --ci"), ("vitest", "npx --no-install vitest run"),
                         ("mocha", "npx --no-install mocha"), ("@playwright/test", "npx --no-install playwright test"),
                         ("cypress", "npx --no-install cypress run")):
            if lib in deps or lib in str(pj.get("scripts", {}).get("test", "")):
                lang = "typescript" if "typescript" in deps or "ts-jest" in deps else "javascript"
                fw.append({"language": lang, "framework": lib.replace("@playwright/test", "playwright"),
                           "evidence": [f"package.json: {lib} {deps.get(lib, '(in test script)')}"], "config_files": ["package.json"],
                           "suggested_command": cmd})
    for build, fwname, cmd in (("pom.xml", "maven", "mvn -B -o test"), ("build.gradle", "gradle", "gradle --offline test"),
                               ("build.gradle.kts", "gradle", "gradle --offline test")):
        if build in fset or has(build):
            txt = read(root, build)
            junit = "junit-jupiter" in txt or "junit" in txt.lower()
            testng = "testng" in txt.lower()
            fw.append({"language": "java", "framework": f"{fwname}+{'testng' if testng else 'junit5' if 'jupiter' in txt else 'junit' if junit else 'junit?'}",
                       "evidence": [build], "config_files": [build], "suggested_command": cmd})
            break
    return fw

def discover(root: str) -> dict:
    root = str(Path(root).resolve())
    files = list(iter_files(root))
    tests = []
    for f in files:
        lang = lang_of(f)
        if lang and is_test_file(f):
            text = read(root, f)
            info = analyze_test(lang, text)
            tests.append({"path": f, "language": lang, "kind": classify_kind(f, text), **info})
    frameworks = detect_frameworks(root, files, tests)
    for t in tests:
        t["framework"] = next((x["framework"] for x in frameworks if x["language"] == t["language"] or
                               (t["language"] in ("javascript", "typescript") and x["language"] in ("javascript", "typescript"))), None)
    sources = [f for f in files if is_source_file(f)]
    graph = build_import_graph(root, files)

    # test -> source mapping: imports first, naming heuristic second
    by_stem = {}
    for s in sources: by_stem.setdefault(Path(s).stem.lower(), []).append(s)
    src_set = set(sources)
    for t in tests:
        rel, mapped = t["path"], {}
        for dep in graph.get(rel, []):
            if dep in src_set: mapped[dep] = "import"
        stem = Path(rel).stem
        stem = re.sub(r"^test_|_test$|\.test$|\.spec$|Tests?$|^Test", "", stem).lower()
        for s in by_stem.get(stem, []): mapped.setdefault(s, "name")
        t["sources"] = [{"path": s, "reason": r} for s, r in sorted(mapped.items())]
    covered = {s["path"] for t in tests for s in t["sources"]}
    untested = [{"path": s, "critical_keyword": bool(CRITICAL.search(s)), "lines": read(root, s).count("\n") + 1}
                for s in sources if s not in covered]
    weak = []
    for t in tests:
        if t.get("syntax_error"): weak.append({"file": t["path"], "kind": "syntax_error", "detail": "test file does not parse"})
        elif t["tests"] and t["assertions"] == 0: weak.append({"file": t["path"], "kind": "no_assertions", "detail": f"{t['tests']} tests, 0 assertions"})
        elif t["tests"] and t["assertions"] / t["tests"] < 1: weak.append({"file": t["path"], "kind": "low_assertion_density", "detail": f"{t['assertions']} assertions / {t['tests']} tests"})
        if t["tests"] and t["skipped"] / t["tests"] >= 0.5: weak.append({"file": t["path"], "kind": "mostly_skipped", "detail": f"{t['skipped']}/{t['tests']} skipped"})
        if t["tests"] == 0 and not t.get("syntax_error"): weak.append({"file": t["path"], "kind": "no_tests_found", "detail": "no test functions detected"})
    for u in untested:
        if u["critical_keyword"]: weak.append({"file": u["path"], "kind": "critical_source_untested", "detail": "name suggests money/auth logic and no test maps to it"})
    pyt = [t for t in tests if t["language"] == "python"]
    dirs = Counter(os.path.dirname(t["path"]) for t in pyt)
    conventions = {"python": {"style": "unittest" if pyt and all(t.get("unittest") for t in pyt) else "pytest-functions",
                              "test_dir": dirs.most_common(1)[0][0] if dirs else "tests", "file_prefix": "test_"}}
    kinds = Counter(t["kind"] for t in tests)
    return {"repo_path": root, "frameworks": frameworks, "test_dirs": sorted({os.path.dirname(t["path"]) for t in tests}),
            "test_files": tests, "sources_total": len(sources), "sources_without_tests": untested,
            "weak_indicators": weak, "import_graph": graph, "conventions": conventions,
            "totals": {"test_files": len(tests), "tests": sum(t["tests"] for t in tests), "assertions": sum(t["assertions"] for t in tests),
                       "by_kind": dict(kinds), "by_language": dict(Counter(t["language"] for t in tests)),
                       "sources_with_tests": len(covered & src_set), "sources_without_tests": len(untested)}}
