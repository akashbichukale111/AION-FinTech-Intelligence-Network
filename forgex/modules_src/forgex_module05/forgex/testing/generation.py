"""B. Intelligent test generation (Python, pytest style). Proposals are reviewable; nothing touches the repo until approved.
Generated tests are derived from the real AST of the changed code: signatures, documented `raise`s and (when a
baseline exists) a differential comparison against the pre-change implementation."""
from __future__ import annotations
import ast, itertools, re
from pathlib import Path

from .coverage import changed_lines
from .regression import _routes

SAMPLES = {"int": ["0", "1", "-1", "2**31"], "str": ['""', '"a"', '" "', '"\\u00e9" * 3'], "float": ["0.0", "-1.5", "1e308"],
           "bool": ["True", "False"], "Decimal": ['Decimal("0")', 'Decimal("0.01")', 'Decimal("-1.00")', 'Decimal("1234567.89")'],
           "list": ["[]", "[1, 2]"], "dict": ["{}", '{"k": 1}'], "bytes": ['b""', 'b"x"'], "tuple": ["()", "(1,)"]}
IMPURE = re.compile(r"\b(random|time\.|datetime\.now|uuid|open\(|requests|socket|subprocess|os\.environ|input\()")
FORBIDDEN_CALLS = {"system", "popen", "Popen", "check_output", "check_call", "rmtree", "remove", "unlink", "urlopen", "connect", "fork", "eval"}
FORBIDDEN_IMPORTS = {"subprocess", "socket", "requests", "urllib", "http", "shutil", "ctypes", "multiprocessing"}

def _samples(ann):
    if ann is None: return None
    t = ann.replace(" ", "")
    opt = False
    m = re.fullmatch(r"(?:Optional\[(.+)\]|(.+)\|None|None\|(.+))", t)
    if m: t, opt = next(g for g in m.groups() if g), True
    t = t.split("[")[0].replace("decimal.", "")
    s = SAMPLES.get(t)
    if s is None: return None
    return s + (["None"] if opt else [])

def _doc_raises(fn):
    names = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Raise) and n.exc is not None:
            e = n.exc.func if isinstance(n.exc, ast.Call) else n.exc
            nm = ast.unparse(e)
            if re.fullmatch(r"[A-Za-z_][\w.]*", nm) and nm[0].isupper() and nm.split(".")[-1].endswith(("Error", "Exception", "Exit")):
                names.add(nm.split(".")[-1])
    return sorted(names)

def _module_path(rel):
    p = rel[:-3].split("/")
    if p[0] in ("src", "lib") and len(p) > 1: p = p[1:]
    if p[-1] == "__init__": p = p[:-1]
    return ".".join(p)

def validate(code):
    issues = []
    try: tree = ast.parse(code)
    except SyntaxError as e: return {"ok": False, "issues": [f"syntax error: {e}"]}
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""]
            for m in mods:
                if m.split(".")[0] in FORBIDDEN_IMPORTS: issues.append(f"forbidden import: {m}")
        elif isinstance(n, ast.Call):
            nm = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
            if nm in FORBIDDEN_CALLS: issues.append(f"forbidden call: {nm}() at line {n.lineno}")
            if nm == "open" and any(isinstance(a, ast.Constant) and isinstance(a.value, str) and re.search(r"[wa+x]", a.value) for a in n.args[1:2]):
                issues.append(f"file write at line {n.lineno}")
    if not any(isinstance(n, ast.FunctionDef) and n.name.startswith("test_") for n in ast.walk(tree)):
        issues.append("no test functions generated")
    return {"ok": not issues, "issues": issues}

def _funcs(text):
    try: tree = ast.parse(text)
    except SyntaxError: return {}, None
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")}, tree

def _combos(params, limit=24):
    pools = [p[1] for p in params]
    full = list(itertools.islice(itertools.product(*pools), 400))
    # always include the all-first "typical" call, then spread the rest deterministically
    step = max(1, len(full) // limit)
    return [full[0]] + [c for i, c in enumerate(full[1:], 1) if i % step == 0][: limit - 1] if full else []

def _gen_function(name, fn, base_fn, base_src, notes):
    params = []
    a = fn.args
    if a.vararg or a.kwarg or a.kwonlyargs:
        notes.append(f"{name}: *args/**kwargs/keyword-only parameters not supported; skipped"); return None
    for p in a.posonlyargs + a.args:
        s = _samples(ast.unparse(p.annotation) if p.annotation else None)
        if s is None:
            notes.append(f"{name}: parameter '{p.arg}' has no annotation with a supported type; skipped"); return None
        params.append((p.arg, s))
    src = ast.get_source_segment(open_text[0], fn) or ""
    allowed = sorted(set(_doc_raises(fn)) | {"TypeError", "ValueError", "ArithmeticError"})
    combos = _combos(params) if params else [()]
    cl = ", ".join(f"({', '.join(c)}{',' if len(c) == 1 else ''})" for c in combos)
    ret = ast.unparse(fn.returns).replace("decimal.", "") if fn.returns else None
    rtype = {"int": "int", "str": "str", "bool": "bool", "float": "float", "Decimal": "Decimal", "list": "list", "dict": "dict"}.get((ret or "").split("[")[0])
    out = [f"_{name.upper()}_ARGS = [{cl}]", f"_{name.upper()}_OK = ({', '.join(allowed)},)", "",
           f"def test_{name}_edge_inputs_raise_only_expected_errors():",
           f"    \"\"\"Edge-case sweep of {len(combos)} input combination(s); only documented/argument errors may be raised.\"\"\"",
           f"    for args in _{name.upper()}_ARGS:", "        try:", f"            result = {name}(*args)",
           f"        except _{name.upper()}_OK:", "            continue"]
    if rtype: out += [f"        assert isinstance(result, {rtype}), f\"{name}{{args}} returned {{type(result).__name__}}, expected {rtype}\""]
    out += ["", f"def test_{name}_typical_call_succeeds_or_raises_documented_error():",
            f"    try:", f"        {name}(*_{name.upper()}_ARGS[0])", f"    except _{name.upper()}_OK:", "        pass", ""]
    if not IMPURE.search(src):
        out += [f"def test_{name}_is_deterministic():", f"    for args in _{name.upper()}_ARGS:", "        try:", f"            first = {name}(*args)",
                f"        except _{name.upper()}_OK as e:", f"            with pytest.raises(type(e)):", f"                {name}(*args)", "            continue",
                f"        assert {name}(*args) == first", ""]
    if base_fn is not None and [p.arg for p in base_fn.args.args] == [p.arg for p in a.args] and base_src:
        bsrc = re.sub(rf"\bdef {name}\(", f"def _baseline_{name}(", base_src, count=1)
        out = [bsrc, ""] + out + [f"def test_{name}_regression_against_previous_implementation():",
              f"    \"\"\"Differential test vs. the pre-change implementation. A failure means behaviour changed: review whether intended.\"\"\"",
              "    _import_module_globals()", f"    for args in _{name.upper()}_ARGS:",
              "        try:", f"            old = ('ok', _baseline_{name}(*args))", "        except Exception as e:", "            old = ('exc', type(e).__name__)",
              "        try:", f"            new = ('ok', {name}(*args))", "        except Exception as e:", "            new = ('exc', type(e).__name__)",
              "        assert new == old, f\"behaviour changed for {args}: before={old} after={new}\"", ""]
    return "\n".join(out)

open_text = [""]

def generate(repo_id, root, changes, conventions=None):
    """Return a list of reviewable proposals (one per changed Python source file)."""
    from .store import new_id, now
    conv = (conventions or {}).get("python", {"test_dir": "tests"})
    proposals, skipped = [], []
    for ch in changes:
        path, after = ch["path"], ch.get("after")
        if not after or not path.endswith(".py") or re.search(r"(^|/)(tests?|migrations?)/|test_|conftest", path):
            if path.endswith((".js", ".jsx", ".ts", ".tsx", ".java")): skipped.append({"path": path, "reason": "test generation is implemented for Python only; JS/TS/Java are discovered and executed but not generated"})
            continue
        open_text[0] = after
        new_f, tree = _funcs(after)
        old_f, _ = _funcs(ch.get("before") or "")
        if tree is None: skipped.append({"path": path, "reason": "file does not parse"}); continue
        lines = set(changed_lines(ch.get("before"), after))
        touched = {n: f for n, f in new_f.items() if lines & set(range(f.lineno, (f.end_lineno or f.lineno) + 1))}
        notes, body, covered = [], [], []
        for n, f in touched.items():
            bfn = old_f.get(n)
            open_text[0] = after
            g = _gen_function(n, f, bfn, ast.get_source_segment(ch.get("before") or "", bfn) if bfn else None, notes)
            if g: body.append(g); covered.append({"function": n, "change": "modified" if bfn else "added"})
        # API contract tests
        app = re.search(r"^(\w+)\s*=\s*FastAPI\(", after, re.M)
        contract = []
        if app:
            now_routes, was = _routes(after), _routes(ch.get("before") or "")
            gets = sorted(p for v, p in now_routes if v == "get" and "{" not in p)
            if was or gets:
                contract.append(f"from fastapi.testclient import TestClient\nfrom {_module_path(path)} import {app.group(1)} as _app\n_client = TestClient(_app)")
                if was:
                    contract.append(f"def test_contract_previously_declared_routes_still_registered():\n    registered = {{r.path for r in _app.routes}}\n"
                                    f"    expected = {sorted(p for _, p in was)!r}\n    missing = [p for p in expected if p not in registered]\n    assert not missing, f\"routes removed or renamed: {{missing}}\"")
                for p in gets[:8]:
                    fn = "test_contract_get_" + re.sub(r"\W+", "_", p).strip("_")
                    contract.append(f"def {fn or 'test_contract_get_root'}():\n    r = _client.get({p!r})\n    assert r.status_code < 500, r.text\n    assert r.headers['content-type'].startswith('application/json') or r.status_code >= 400")
        if not body and not contract:
            if lines: skipped.append({"path": path, "reason": "; ".join(notes) or "no changed function eligible for generation"})
            continue
        mod = _module_path(path)
        names = sorted(c["function"] for c in covered)
        depth = len(Path(conv["test_dir"]).parts) if conv["test_dir"] not in ("", ".") else 0
        pid = new_id("prop")
        header = [f"# Generated by FORGEX Module 05 (proposal {pid}) — REVIEW BEFORE MERGING.", f"# Source: {path}. Style: pytest functions (repo convention).",
                  "import sys, pathlib", "import pytest", "from decimal import Decimal",
                  f"_ROOT = pathlib.Path(__file__).resolve().parents[{depth}]", "sys.path.insert(0, str(_ROOT)); sys.path.insert(0, str(_ROOT / 'src'))"]
        if names:
            header += [f"import {mod} as _mod", f"from {mod} import {', '.join(names)}", "",
                       "def _import_module_globals():", "    g = globals()", "    for k, v in vars(_mod).items():",
                       "        if not k.startswith('__') and k not in g: g[k] = v", ""]
        code = "\n".join(header) + "\n" + "\n\n".join(body + contract) + "\n"
        tpath = f"{conv['test_dir']}/test_{Path(path).stem}_generated.py".lstrip("/")
        proposals.append({"id": pid, "repository_id": repo_id, "status": "proposed", "source_file": path, "test_path": tpath, "code": code,
                          "kinds": sorted({k for k in ("unit", "edge-case", "regression" if any("regression_against" in b for b in body) else None, "api-contract" if contract else None) if k}),
                          "functions": covered, "notes": notes, "validation": validate(code), "created_at": now(), "reviews": []})
    return proposals, skipped
