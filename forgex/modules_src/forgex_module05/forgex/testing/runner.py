"""C. Safe test execution. Tests only ever run in an isolated copy of the repo.
Isolation order: Docker (no network, read-only root, cap-drop, pids/mem/cpu limits) > Linux user+net namespace
(`unshare -rn`) with rlimits and a scrubbed environment. If neither can cut network access, the run is refused unless the
caller explicitly accepts that risk. Never runs on the host working tree."""
from __future__ import annotations
import glob, importlib.util, os, re, resource, shlex, shutil, signal, subprocess, sys, tempfile, time
import xml.etree.ElementTree as ET
from pathlib import Path

class IsolationUnavailable(RuntimeError): pass

_ISO = None
def detect_isolation():
    global _ISO
    if _ISO: return _ISO
    docker = netns = False
    if shutil.which("docker"):
        try: docker = subprocess.run(["docker", "info"], capture_output=True, timeout=8).returncode == 0
        except Exception: pass
    if shutil.which("unshare"):
        try: netns = subprocess.run(["unshare", "-r", "-n", "true"], capture_output=True, timeout=8).returncode == 0
        except Exception: pass
    _ISO = {"docker": docker, "netns": netns}
    return _ISO

def choose_mode(requested="auto", allow_unisolated_network=False):
    iso = detect_isolation()
    mode = requested
    if mode == "auto": mode = "docker" if iso["docker"] else "process"
    if mode == "docker" and not iso["docker"]: raise IsolationUnavailable("docker is not available")
    net_isolated = mode == "docker" or iso["netns"]
    if mode == "process" and not net_isolated and not allow_unisolated_network:
        raise IsolationUnavailable("no network isolation available (need docker or `unshare -rn`). Re-run with allow_unisolated_network=true only for repositories you trust.")
    return {"mode": mode, "network_isolated": net_isolated, "filesystem": "isolated copy" if mode == "process" else "isolated copy, read-only container root"}

# ------------------------------------------------------------ redaction
SECRET_KEY = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|COOKIE", re.I)
PATTERNS = [re.compile(p) for p in (r"AKIA[0-9A-Z]{16}", r"gh[pousr]_[A-Za-z0-9]{20,}", r"sk-[A-Za-z0-9_-]{20,}", r"xox[baprs]-[A-Za-z0-9-]{10,}",
                                    r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{16,}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
                                    r"(?i)(password|passwd|secret|api[_-]?key|token)\s*[=:]\s*['\"]?[^\s'\"]{6,}")]
def redact(text):
    for k, v in os.environ.items():
        if SECRET_KEY.search(k) and len(v) >= 6: text = text.replace(v, "[REDACTED]")
    for p in PATTERNS: text = p.sub(lambda m: "[REDACTED]" if not m.groups() else f"{m.group(1)}=[REDACTED]", text)
    return text

# ------------------------------------------------------------ commands
def default_commands(framework, py, collect_cov, mode):
    fw = framework.split("+")[0]
    cov = collect_cov and mode == "process" and importlib.util.find_spec("coverage") is not None
    if fw == "pytest":
        t = ["-m", "pytest", "-q", "-p", "no:cacheprovider", "--junitxml=junit.xml"]
        if cov: return [[py, "-m", "coverage", "run", "--branch", *t], [py, "-m", "coverage", "xml", "-o", "coverage.xml"]]
        return [[py, *t]]
    if fw == "unittest": return [[py, "-m", "unittest", "discover", "-v"]]
    if fw == "jest": return [["npx", "--no-install", "jest", "--ci", "--json", "--outputFile=.forgex-jest.json"] + (["--coverage", "--coverageReporters=lcov"] if collect_cov else [])]
    if fw == "vitest": return [["npx", "--no-install", "vitest", "run", "--reporter=junit", "--outputFile=junit.xml"]]
    if fw == "maven": return [["mvn", "-B", "-o", "test"]]
    if fw == "gradle": return [["gradle", "--offline", "test"]]
    return None

def _preexec(mem_bytes, cpu_s, limit_as):
    def f():
        os.setsid()
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
        resource.setrlimit(resource.RLIMIT_FSIZE, (256 << 20, 256 << 20))
        resource.setrlimit(resource.RLIMIT_NOFILE, (1024, 1024))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if limit_as: resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
    return f

def run_command(cmd, ws, iso, timeout=300, memory_mb=1024, name="forgex"):
    """Run one command inside the sandbox. Returns exit code, timeout flag, duration and redacted/capped output."""
    logs = tempfile.mkdtemp(prefix="forgex_logs_")
    so, se = open(os.path.join(logs, "out"), "wb"), open(os.path.join(logs, "err"), "wb")
    tmp = os.path.join(ws, ".tmp"); os.makedirs(tmp, exist_ok=True)
    started, timed_out, code, start_error = time.monotonic(), False, None, None
    try:
        if iso["mode"] == "docker":
            container = f"forgex_{name}"
            full = ["docker", "run", "--rm", "--name", container, "--network", "none", "--memory", f"{memory_mb}m", "--cpus", "1", "--pids-limit", "256",
                    "--read-only", "--tmpfs", "/tmp:rw,size=256m", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", f"{os.getuid()}:{os.getgid()}",
                    "-v", f"{ws}:/work", "-w", "/work", "-e", "HOME=/tmp", "-e", "CI=true", os.environ.get("FORGEX_TEST_IMAGE", "python:3.12-slim"), *cmd]
            proc = subprocess.Popen(full, stdout=so, stderr=se, stdin=subprocess.DEVNULL, start_new_session=True)
        else:
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": ws, "TMPDIR": tmp, "LANG": "C.UTF-8", "CI": "true", "NO_COLOR": "1",
                   "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": ws, "FORGEX_SANDBOX": "1"}
            c = list(cmd)
            if c[0] in ("python", "python3"): c[0] = sys.executable
            if iso["network_isolated"]: c = ["unshare", "-r", "-n", "--", *c]
            as_ok = os.path.basename(cmd[0]).startswith("python")
            proc = subprocess.Popen(c, cwd=ws, env=env, stdout=so, stderr=se, stdin=subprocess.DEVNULL,
                                    preexec_fn=_preexec(memory_mb << 20, timeout + 30, as_ok))
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try: os.killpg(proc.pid, signal.SIGKILL)
            except Exception: pass
            if iso["mode"] == "docker": subprocess.run(["docker", "kill", f"forgex_{name}"], capture_output=True)
            proc.wait(timeout=10)
        else:
            if code is not None and code < 0: pass
    except (FileNotFoundError, PermissionError, OSError) as e:
        start_error = f"{type(e).__name__}: {e}"
    finally:
        so.close(); se.close()
    def tail(p, cap=200_000):
        b = open(p, "rb").read()
        s = b[-cap:].decode("utf-8", "replace")
        return redact(("[... truncated ...]\n" if len(b) > cap else "") + s)
    out, err = tail(os.path.join(logs, "out")), tail(os.path.join(logs, "err"))
    shutil.rmtree(logs, ignore_errors=True)
    if code is not None and code < 0 and not timed_out:
        err += f"\n[process killed by signal {-code}" + (" (CPU/memory/file-size limit)]" if -code in (signal.SIGKILL, signal.SIGXCPU, signal.SIGXFSZ, signal.SIGSEGV) else "]")
    return {"command": " ".join(shlex.quote(x) for x in cmd), "exit_code": code, "timed_out": timed_out, "start_error": start_error,
            "duration_seconds": round(time.monotonic() - started, 3), "stdout": out, "stderr": err}

# ------------------------------------------------------------ result parsing
def _guess_file(ws, classname):
    parts = (classname or "").split(".")
    while parts:
        cand = "/".join(parts) + ".py"
        if (Path(ws) / cand).is_file(): return cand
        parts.pop()
    return None

def parse_junit(ws):
    res, seen = [], set()
    pats = ["junit*.xml", "**/surefire-reports/TEST-*.xml", "**/test-results/**/*.xml", "**/failsafe-reports/TEST-*.xml"]
    for pat in pats:
        for f in sorted(glob.glob(os.path.join(ws, pat), recursive=True)):
            if f in seen or "node_modules" in f: continue
            seen.add(f)
            try: root = ET.parse(f).getroot()
            except ET.ParseError: continue
            for tc in root.iter("testcase"):
                cn, nm = tc.get("classname", ""), tc.get("name", "")
                status, msg, det = "passed", None, None
                for tag, st in (("failure", "failed"), ("error", "error"), ("skipped", "skipped")):
                    el = tc.find(tag)
                    if el is not None: status, msg, det = st, el.get("message"), el.text
                    if el is not None: break
                res.append({"name": f"{cn}::{nm}" if cn else nm, "classname": cn, "file": tc.get("file") or _guess_file(ws, cn), "status": status,
                            "duration": float(tc.get("time") or 0), "message": redact(msg or "") or None, "details": redact(det or "")[:20000] or None})
    return res, sorted(os.path.relpath(s, ws) for s in seen)

def parse_jest(ws):
    p = os.path.join(ws, ".forgex-jest.json")
    if not os.path.isfile(p): return [], []
    import json
    try: data = json.load(open(p))
    except ValueError: return [], []
    res = []
    for tr in data.get("testResults", []):
        rel = os.path.relpath(tr.get("name", ""), ws)
        ars = tr.get("assertionResults", [])
        if not ars and tr.get("status") == "failed":
            res.append({"name": f"{rel}::<suite>", "classname": rel, "file": rel, "status": "error", "duration": 0, "message": redact((tr.get("message") or "")[:300]), "details": redact(tr.get("message") or "")[:20000]})
        for a in ars:
            st = {"passed": "passed", "failed": "failed", "pending": "skipped", "skipped": "skipped", "todo": "skipped"}.get(a.get("status"), "error")
            fm = "\n".join(a.get("failureMessages") or [])
            res.append({"name": f"{rel}::{a.get('fullName') or a.get('title')}", "classname": rel, "file": rel, "status": st, "duration": (a.get("duration") or 0) / 1000,
                        "message": redact(fm.splitlines()[0]) if fm else None, "details": redact(fm)[:20000] or None})
    return res, [".forgex-jest.json"]

def parse_unittest(text):
    res, details = [], {}
    for m in re.finditer(r"^(?:FAIL|ERROR): (\w+) \(([\w.]+)\)\n-+\n([\s\S]*?)(?=\n={10,}|\n-{10,}\nRan )", text, re.M): details[(m.group(1), m.group(2))] = m.group(3)
    for m in re.finditer(r"^(\w+) \(([\w.]+)\)(?:\n.*?)? \.\.\. (ok|FAIL|ERROR|skipped.*)$", text, re.M):
        nm, cls, st = m.groups()
        status = "passed" if st == "ok" else "failed" if st == "FAIL" else "error" if st == "ERROR" else "skipped"
        d = details.get((nm, cls))
        res.append({"name": f"{cls}", "classname": ".".join(cls.split(".")[:-1]), "file": None, "status": status, "duration": 0,
                    "message": redact(next((l for l in (d or "").splitlines() if l.strip() and not l.startswith(("Traceback", "  "))), "") or "") or None, "details": redact(d or "")[:20000] or None})
    return res

def collect_results(ws, framework, outputs):
    res, ev = parse_jest(ws) if framework.startswith("jest") else parse_junit(ws)
    if not res and framework == "unittest": res, ev = parse_unittest(outputs), ["stdout (unittest -v)"]
    return res, ev
