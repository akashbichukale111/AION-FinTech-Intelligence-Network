"""Provider-independent agents. Deterministic provider is real; LLM provider is honestly reported unavailable."""
import ast, os, re, subprocess, sys
from .workspace import safe_path

ROLES = [("engineering_lead", "Understands approved plan, coordinates work"),
         ("code_analysis", "Inspects relevant files and imports"),
         ("implementation", "Applies code modifications"),
         ("refactoring", "Restructures while preserving behaviour"),
         ("test_engineering", "Runs relevant tests"),
         ("code_review", "Independent review of the diff"),
         ("security_review", "Security review of the diff")]

class AgentProvider:
    name = "base"; available = False; reason = ""
    def apply_step(self, ws, step): raise NotImplementedError

class DeterministicProvider(AgentProvider):
    """Executes explicit plan operations (replace/create/append). No intelligence is claimed."""
    name = "deterministic"; available = True
    def apply_step(self, ws, step):
        touched = []
        for op in step.get("ops", []):
            p = safe_path(ws, op["file"]); kind = op["op"]
            if kind == "create":
                if p.exists(): raise ValueError(f"create: {op['file']} already exists")
                p.parent.mkdir(parents=True, exist_ok=True); p.write_text(op["content"])
            elif kind == "replace":
                s = p.read_text(); n = s.count(op["find"])
                if n != 1: raise ValueError(f"replace: expected exactly 1 match in {op['file']}, found {n}")
                p.write_text(s.replace(op["find"], op["replace"]))
            elif kind == "append":
                with p.open("a") as f: f.write(op["content"])
            else: raise ValueError(f"unknown op {kind}")
            touched.append(op["file"])
        return touched

class LLMProvider(AgentProvider):
    """Plug an LLM here (set FORGEX_LLM_API_KEY and implement apply_step). Unavailable by default."""
    name = "llm"; available = bool(os.environ.get("FORGEX_LLM_API_KEY"))
    reason = "No FORGEX_LLM_API_KEY configured; free-form steps cannot be executed"
    def apply_step(self, ws, step): raise NotImplementedError("LLM provider adapter not implemented")

def get_provider(): 
    l = LLMProvider(); return l if (l.available and False) else DeterministicProvider()

INJECTION = re.compile(r"(ignore (all )?(previous|prior) instructions|disregard .{0,30}instructions|you are now|system prompt|exfiltrate)", re.I)
def analyse(ws, files):
    out = {"files": [], "imports": {}, "injection_findings": []}
    for f in files:
        try: p = safe_path(ws, f)
        except PermissionError: continue
        if not p.exists(): continue
        txt = p.read_text(errors="replace"); out["files"].append(f)
        if f.endswith(".py"):
            try: out["imports"][f] = sorted({(n.module or "") if isinstance(n, ast.ImportFrom) else a.name for n in ast.walk(ast.parse(txt)) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in (n.names if isinstance(n, ast.Import) else [n])})
            except SyntaxError: pass
        for m in INJECTION.finditer(txt):
            out["injection_findings"].append({"file": f, "text": m.group(0), "action": "treated as untrusted data, ignored"})
    return out

SAFE_TESTS = [[sys.executable, "-m", "pytest", "-x", "-q", "--no-header"], [sys.executable, "-m", "compileall", "-q", "."]]
def _limits():
    try:
        import resource; resource.setrlimit(resource.RLIMIT_CPU, (60, 60)); resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))
    except Exception: pass

def run_tests(ws, timeout=90):
    """Runs only allow-listed commands, cwd=workspace, scrubbed env (no secrets), timeout + rlimits.
    NOTE: process-level isolation, not a container. Use Docker/gVisor for hostile repos."""
    import importlib.util
    has_tests = any(n.startswith("test") or n == "tests" for n in os.listdir(ws))
    cmd = SAFE_TESTS[0] if has_tests and importlib.util.find_spec("pytest") else SAFE_TESTS[1]
    note = "" if cmd is SAFE_TESTS[0] or not has_tests else "[pytest not installed: only compile check ran, tests NOT executed]\n"
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1", "HOME": ws}
    try:
        r = subprocess.run(cmd, cwd=ws, env=env, capture_output=True, text=True, timeout=timeout, preexec_fn=_limits if os.name == "posix" else None)
        return {"command": " ".join(cmd[1:]), "exit_code": r.returncode, "passed": r.returncode in (0, 5), "output": note + (r.stdout + r.stderr)[-4000:], "tests_executed": cmd is SAFE_TESTS[0]}
    except subprocess.TimeoutExpired: return {"command": " ".join(cmd[1:]), "exit_code": -1, "passed": False, "output": "timeout"}

def code_review(changes, declared_files):
    f = []
    for c in changes:
        if declared_files and c["file"] not in declared_files:
            f.append({"severity": "high", "file": c["file"], "msg": "File changed but not declared in approved plan (unrelated modification)"})
        if c["file"].endswith(".py"):
            try: ast.parse(open(c["_abs"]).read())
            except SyntaxError as e: f.append({"severity": "high", "file": c["file"], "msg": f"Syntax error: {e.msg} line {e.lineno}"})
            except Exception: pass
        if c["additions"] + c["deletions"] > 400: f.append({"severity": "medium", "file": c["file"], "msg": "Large change; split into smaller atomic steps"})
        if re.search(r"^\+.*\b(TODO|FIXME|XXX)\b", c["diff"], re.M): f.append({"severity": "low", "file": c["file"], "msg": "Added TODO/FIXME"})
    return f

SEC_RULES = [
 (r"AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"]{8,}", "critical", "Possible hardcoded secret"),
 (r"\b(eval|exec)\s*\(", "high", "Dynamic code execution"),
 (r"shell\s*=\s*True", "high", "subprocess with shell=True"),
 (r"verify\s*=\s*False", "high", "TLS verification disabled"),
 (r"float\(.*\b(amount|price|balance|total|fee)", "high", "float used for money - use Decimal"),
 (r"(signature|hmac|digest)\w*\s*==\s*", "high", "Non-constant-time signature compare - use hmac.compare_digest"),
 (r"(SELECT|INSERT|UPDATE|DELETE)[^\n]*(%s|\{|\+\s*\w+)[^\n]*(execute|query)", "medium", "Possible SQL string building"),
]
def security_review(changes):
    out = []
    for c in changes:
        for ln in c["diff"].splitlines():
            if not ln.startswith("+") or ln.startswith("+++"): continue
            for rx, sev, msg in SEC_RULES:
                if re.search(rx, ln, re.I): out.append({"severity": sev, "file": c["file"], "msg": msg, "line": ln[1:].strip()[:120].replace("\"", "'") if sev != "critical" else "[redacted]"})
    return out
