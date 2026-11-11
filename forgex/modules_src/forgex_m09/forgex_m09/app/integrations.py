"""Optional bridges to FORGEX Modules 01-08. If a module URL/file is not configured the result says so explicitly
(nothing is faked). ADAPT `ROUTES` to the real FORGEX route names - they are the only coupling point."""
import json, os, urllib.request

ROUTES = {  # (module, purpose): (method, path)
    (1, "architecture"): ("GET", "/api/v1/repositories/architecture"),
    (1, "code_context"): ("POST", "/api/v1/repositories/search"),
    (2, "create_task"): ("POST", "/api/v1/plans"),
    (3, "prepare_change"): ("POST", "/api/v1/autonomous-coding/jobs"),
    (5, "validate"): ("POST", "/api/v1/testing/runs"),
    (6, "transactions"): ("GET", "/api/v1/transaction-reliability/summary"),
    (7, "api_webhook_findings"): ("GET", "/api/v1/api-intelligence/findings"),
    (8, "security_findings"): ("GET", "/api/v1/security/findings"),
}

def call(module, purpose, payload=None, timeout=5):
    base = os.getenv(f"FORGEX_M{module:02d}_URL")
    if not base: return {"available": False, "reason": f"FORGEX_M{module:02d}_URL not configured"}
    method, path = ROUTES[(module, purpose)]
    req = urllib.request.Request(base.rstrip("/") + path, method=method, data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Content-Type": "application/json", "X-API-Key": os.getenv("FORGEX_INTERNAL_KEY", "")})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return {"available": True, "data": json.loads(r.read().decode())}
    except Exception as e: return {"available": False, "reason": f"module {module} call failed: {type(e).__name__}"}

def architecture():
    """Module 01 evidence: {"services": {svc: {"files": [...]}}, "edges": [{"source","target","kind"}]}.
    Source order: FORGEX_M01_ARCH_FILE (JSON export) -> Module 01 HTTP API."""
    p = os.getenv("FORGEX_M01_ARCH_FILE")
    if p and os.path.exists(p):
        try: return {"available": True, "data": json.load(open(p))}
        except (OSError, ValueError): return {"available": False, "reason": "architecture file unreadable"}
    return call(1, "architecture")

def code_context(service):
    a = architecture()
    if not a.get("available"): return {"available": False, "reason": a.get("reason")}
    files = ((a["data"].get("services") or {}).get(service) or {}).get("files") or []
    return {"available": bool(files), "files": files[:20], "reason": None if files else "service not found in repository architecture"}
