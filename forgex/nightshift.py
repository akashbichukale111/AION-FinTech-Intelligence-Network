"""FORGEX Module 04 - NightShift core (FastAPI + SQLite, stdlib scheduler/worker).
Run: uvicorn nightshift:app   (pip install fastapi uvicorn)
Wire Modules 01-03 via set_adapters(). Unwired adapters => run is BLOCKED, never faked.
"""
import json, sqlite3, threading, time, uuid, os
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeout
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Optional

DB = os.environ.get("NIGHTSHIFT_DB", "nightshift.db")
LEASE_S, TICK_S, STEP_TIMEOUT_S = 120, 15, 600
HIGH_RISK_TAGS = {"migration", "production", "security", "payment_config", "database"}
POLICIES = {
    0: "Monitor - read-only analysis and reporting",
    1: "Investigate - findings and plans (Module 01/02)",
    2: "Prepare - patches + checks in isolated workspace (Module 03)",
    3: "Approved Execution - only explicitly approved plans, low-risk auto only if allow_auto_low",
}

def now(): return datetime.now(timezone.utc)
def iso(d): return d.astimezone(timezone.utc).isoformat() if d else None
def uid(): return uuid.uuid4().hex

# ---------- persistence ----------
_lock = threading.RLock()
def db():
    c = sqlite3.connect(DB, timeout=30, check_same_thread=False, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c
_conn = None
def conn():
    global _conn
    if _conn is None: _conn = db(); init()
    return _conn
def q(sql, a=()):
    with _lock: return [dict(r) for r in conn().execute(sql, a).fetchall()]
def x(sql, a=()):
    with _lock: return conn().execute(sql, a).rowcount

def init():
    conn().executescript("""
    CREATE TABLE IF NOT EXISTS schedules(id TEXT PRIMARY KEY, repo_id TEXT, task_type TEXT, tz TEXT,
      kind TEXT, spec TEXT, window_start TEXT, window_end TEXT, priority INT, policy_level INT,
      allow_auto_low INT DEFAULT 0, paused INT DEFAULT 0, next_run TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, schedule_id TEXT, scheduled_for TEXT, trigger TEXT,
      status TEXT, started_at TEXT, finished_at TEXT, lease_until TEXT, error TEXT, policy_level INT, repo_id TEXT,
      UNIQUE(schedule_id, scheduled_for));
    CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts TEXT, stage TEXT, level TEXT, message TEXT, data TEXT);
    CREATE TABLE IF NOT EXISTS findings(id TEXT PRIMARY KEY, run_id TEXT, repo_id TEXT, category TEXT, title TEXT,
      evidence TEXT, impact TEXT, confidence REAL, status TEXT, task_id TEXT, plan_id TEXT);
    CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, run_id TEXT, plan_id TEXT, risk TEXT, reason TEXT,
      status TEXT DEFAULT 'pending', decided_by TEXT, decided_at TEXT);
    CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY, run_id TEXT UNIQUE, created_at TEXT, body TEXT);
    """)

def log(run_id, stage, msg, level="info", data=None):
    x("INSERT INTO events(run_id,ts,stage,level,message,data) VALUES(?,?,?,?,?,?)",
      (run_id, iso(now()), stage, level, msg, json.dumps(data or {})))

# ---------- Module 01/02/03 adapter contracts ----------
class AdapterNotConfigured(RuntimeError): pass

class Module01(ABC):
    @abstractmethod
    def snapshot(self, repo_id: str, task_type: str) -> list[dict]:
        """Return findings: {category,title,evidence:[...],impact,confidence(0-1),tags:[...]}"""
class Module02(ABC):
    @abstractmethod
    def create_task(self, repo_id: str, finding: dict) -> str: ...
    @abstractmethod
    def generate_plan(self, task_id: str) -> dict:
        """-> {plan_id, files:[...], tags:[...], risk:'low|medium|high'}"""
    @abstractmethod
    def is_approved(self, plan_id: str) -> bool: ...
class Module03(ABC):
    @abstractmethod
    def prepare(self, plan_id: str, timeout_s: int) -> dict:
        """Isolated workspace: -> {workspace, patch_ref, diff_stat, checks:{name:bool}, review:[...], rollback_ref}"""
    @abstractmethod
    def execute(self, plan_id: str, authorization: dict, timeout_s: int) -> dict:
        """-> {verified:bool, patch_ref, changed_files, rollback_ref, details}"""

class _Missing:
    def __getattr__(self, n): raise AdapterNotConfigured(f"Module adapter not wired (needed: {n})")
A = {"m1": _Missing(), "m2": _Missing(), "m3": _Missing()}
def set_adapters(m1: Module01, m2: Module02, m3: Module03): A.update(m1=m1, m2=m2, m3=m3)

# ---------- scheduling ----------
def next_run_after(kind, spec, tz, after: datetime) -> Optional[datetime]:
    z = ZoneInfo(tz); local = after.astimezone(z)
    if kind == "once":
        t = datetime.fromisoformat(spec["at"]);
        t = t if t.tzinfo else t.replace(tzinfo=z)
        return t.astimezone(timezone.utc) if t > after else None
    if kind == "custom": return after + timedelta(minutes=int(spec["every_minutes"]))
    hh, mm = map(int, spec.get("time", "02:00").split(":"))
    days = spec.get("days", list(range(7))) if kind == "weekly" else list(range(7))
    for i in range(0, 9):
        d = (local + timedelta(days=i)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        if d > local and d.weekday() in days: return d.astimezone(timezone.utc)
    return None

def in_window(s, at: datetime) -> bool:
    if not s["window_start"] or not s["window_end"]: return True
    t = at.astimezone(ZoneInfo(s["tz"])).strftime("%H:%M"); a, b = s["window_start"], s["window_end"]
    return a <= t <= b if a <= b else (t >= a or t <= b)

def validate_schedule(d):
    ZoneInfo(d["tz"])
    assert d["kind"] in ("once", "daily", "weekly", "custom"), "bad kind"
    assert d["policy_level"] in POLICIES, "bad policy level"
    assert d["task_type"], "task_type required"
    if d["kind"] == "custom": assert int(d["spec"]["every_minutes"]) >= 5, "min 5 minutes"
    if next_run_after(d["kind"], d["spec"], d["tz"], now()) is None: raise AssertionError("no future run time")

def create_schedule(d):
    d = {"tz": "UTC", "priority": 5, "policy_level": 0, "allow_auto_low": False, "spec": {},
         "window_start": None, "window_end": None, **d}
    validate_schedule(d)
    sid = uid(); nr = next_run_after(d["kind"], d["spec"], d["tz"], now())
    x("INSERT INTO schedules VALUES(?,?,?,?,?,?,?,?,?,?,?,0,?,?)",
      (sid, d["repo_id"], d["task_type"], d["tz"], d["kind"], json.dumps(d["spec"]), d["window_start"], d["window_end"],
       d["priority"], d["policy_level"], int(d["allow_auto_low"]), iso(nr), iso(now())))
    return get_schedule(sid)

def get_schedule(sid):
    r = q("SELECT * FROM schedules WHERE id=?", (sid,));
    if not r: return None
    r = r[0]; r["spec"] = json.loads(r["spec"]); return r

def update_schedule(sid, patch):
    cur = get_schedule(sid)
    if not cur: return None
    allowed = {"repo_id", "task_type", "tz", "kind", "spec", "window_start", "window_end", "priority", "policy_level", "allow_auto_low"}
    new = {**cur, **{k: v for k, v in patch.items() if k in allowed}}
    validate_schedule(new)
    nr = next_run_after(new["kind"], new["spec"], new["tz"], now())
    x("UPDATE schedules SET repo_id=?,task_type=?,tz=?,kind=?,spec=?,window_start=?,window_end=?,priority=?,policy_level=?,allow_auto_low=?,next_run=? WHERE id=?",
      (new["repo_id"], new["task_type"], new["tz"], new["kind"], json.dumps(new["spec"]), new["window_start"], new["window_end"],
       new["priority"], new["policy_level"], int(new["allow_auto_low"]), iso(nr), sid))
    return get_schedule(sid)

def set_paused(sid, paused):
    if paused: x("UPDATE schedules SET paused=1 WHERE id=?", (sid,))
    else:  # resume: recompute from now so missed slots are not replayed
        s = get_schedule(sid)
        if s: x("UPDATE schedules SET paused=0,next_run=? WHERE id=?",
                (iso(next_run_after(s["kind"], s["spec"], s["tz"], now())), sid))
    return get_schedule(sid)

def enqueue_due():
    """Idempotent: UNIQUE(schedule_id, scheduled_for) prevents duplicates even with multiple schedulers."""
    for s in q("SELECT * FROM schedules WHERE paused=0 AND next_run IS NOT NULL AND next_run<=?", (iso(now()),)):
        spec = json.loads(s["spec"]); slot = datetime.fromisoformat(s["next_run"])
        status = "queued" if in_window(s, now()) else "skipped"
        x("INSERT OR IGNORE INTO runs(id,schedule_id,scheduled_for,trigger,status,policy_level,repo_id) VALUES(?,?,?,?,?,?,?)",
          (uid(), s["id"], s["next_run"], "schedule", status, s["policy_level"], s["repo_id"]))
        nr = next_run_after(s["kind"], spec, s["tz"], max(now(), slot))
        x("UPDATE schedules SET next_run=? WHERE id=? AND next_run=?", (iso(nr), s["id"], s["next_run"]))

def manual_run(schedule_id, user):
    s = get_schedule(schedule_id)
    if not s: return None
    if q("SELECT 1 FROM runs WHERE schedule_id=? AND status IN('queued','running')", (schedule_id,)):
        raise ValueError("conflict: a run for this schedule is already queued/running")
    rid = uid()
    x("INSERT INTO runs(id,schedule_id,scheduled_for,trigger,status,policy_level,repo_id) VALUES(?,?,?,?,?,?,?)",
      (rid, schedule_id, f"manual:{rid}", f"manual:{user}", "queued", s["policy_level"], s["repo_id"]))
    return rid

# ---------- recovery ----------
def recover_expired():
    """Worker crash / interruption: expired lease => 'interrupted'. NEVER 'succeeded'."""
    for r in q("SELECT id FROM runs WHERE status='running' AND lease_until<?", (iso(now()),)):
        x("UPDATE runs SET status='interrupted',finished_at=?,error='worker lease expired' WHERE id=? AND status='running'", (iso(now()), r["id"]))
        log(r["id"], "recovery", "run interrupted (lease expired); not marked successful", "error")
        build_report(r["id"])

def claim_next():
    rows = q("""SELECT r.id FROM runs r JOIN schedules s ON s.id=r.schedule_id WHERE r.status='queued'
                ORDER BY s.priority DESC, r.rowid LIMIT 1""")
    if not rows: return None
    rid = rows[0]["id"]
    # conflict guard: one active run per repo
    run = q("SELECT repo_id FROM runs WHERE id=?", (rid,))[0]
    if q("SELECT 1 FROM runs WHERE status='running' AND repo_id=?", (run["repo_id"],)): return None
    n = x("UPDATE runs SET status='running',started_at=?,lease_until=? WHERE id=? AND status='queued'",
          (iso(now()), iso(now() + timedelta(seconds=LEASE_S)), rid))
    return rid if n else None

def heartbeat(rid): x("UPDATE runs SET lease_until=? WHERE id=? AND status='running'", (iso(now() + timedelta(seconds=LEASE_S)), rid))

# ---------- policy ----------
def evaluate_policy(level, stage, plan=None, allow_auto_low=False, approved=False):
    """Returns (allowed, reason). stage in discover|plan|prepare|execute."""
    need = {"discover": 0, "plan": 1, "prepare": 2, "execute": 3}[stage]
    if level < need: return False, f"policy level {level} < required {need} for {stage}"
    if stage == "execute" and plan:
        tags = set(plan.get("tags", []))
        if "payment_config" in tags: return False, "live payment configuration is never auto-modified"
        high = plan.get("risk") == "high" or tags & HIGH_RISK_TAGS
        if not approved: return False, "plan not approved in Module 02"
        if high: return True, "high-risk: gated by human approval in coordinator"
    return True, "allowed"

def needs_human(plan, approved, allow_auto_low):
    tags = set(plan.get("tags", [])); high = plan.get("risk") == "high" or bool(tags & HIGH_RISK_TAGS)
    if "payment_config" in tags: return True
    if high: return True
    return not (approved and (allow_auto_low or approved))

def timed(fn, *a):
    with ThreadPoolExecutor(1) as ex:
        f = ex.submit(fn, *a)
        try: return f.result(timeout=STEP_TIMEOUT_S + 5)
        except FTimeout: raise TimeoutError(f"step exceeded {STEP_TIMEOUT_S}s")

# ---------- discovery + coordinator ----------
def classify(f):
    ev = f.get("evidence") or []
    if not ev: return None                                  # no evidence => not a finding
    return "confirmed" if (f.get("confidence", 0) >= 0.85 and len(ev) >= 1) else "suspected"

def run_pipeline(rid):
    run = q("SELECT * FROM runs WHERE id=?", (rid,))[0]; s = get_schedule(run["schedule_id"]); lvl = run["policy_level"]
    final = "succeeded"; touched = set()
    try:
        log(rid, "policy", f"level {lvl}: {POLICIES[lvl]}")
        try: raw = timed(A["m1"].snapshot, s["repo_id"], s["task_type"])
        except AdapterNotConfigured as e: raise
        log(rid, "monitor", f"Module 01 returned {len(raw)} candidate findings")
        findings = []
        for f in raw:
            st = classify(f)
            if not st: log(rid, "discover", f"dropped (no evidence): {f.get('title')}", "warn"); continue
            fid = uid(); findings.append((fid, f, st))
            x("INSERT INTO findings VALUES(?,?,?,?,?,?,?,?,?,NULL,NULL)",
              (fid, rid, s["repo_id"], f["category"], f["title"], json.dumps(f["evidence"]), f.get("impact", ""), f.get("confidence", 0), st))
        for fid, f, st in sorted(findings, key=lambda t: -t[1].get("confidence", 0)):
            heartbeat(rid)
            ok, why = evaluate_policy(lvl, "plan")
            if not ok: log(rid, "policy", why); continue
            if st != "confirmed": log(rid, "plan", f"skip plan (suspected, needs triage): {f['title']}"); continue
            tid = timed(A["m2"].create_task, s["repo_id"], f); plan = timed(A["m2"].generate_plan, tid)
            pid = plan["plan_id"]; x("UPDATE findings SET task_id=?,plan_id=? WHERE id=?", (tid, pid, fid))
            log(rid, "plan", f"plan {pid} for task {tid}", data=plan)
            if touched & set(plan.get("files", [])):
                log(rid, "plan", f"conflict: {pid} overlaps files of earlier plan; deferred", "warn"); continue
            ok, why = evaluate_policy(lvl, "prepare")
            if not ok: log(rid, "policy", why); continue
            prep = timed(A["m3"].prepare, pid, STEP_TIMEOUT_S)
            log(rid, "prepare", f"patch {prep.get('patch_ref')} checks={prep.get('checks')}", data=prep)
            if not all((prep.get("checks") or {"_": False}).values()):
                log(rid, "prepare", "checks failed; not executing", "error"); final = "completed_with_failures"; continue
            approved = A["m2"].is_approved(pid)
            tags = set(plan.get("tags", []))
            high = plan.get("risk") == "high" or bool(tags & HIGH_RISK_TAGS)
            human_ok = bool(q("SELECT 1 FROM approvals WHERE plan_id=? AND status='approved'", (pid,)))
            ok, why = evaluate_policy(lvl, "execute", plan, s["allow_auto_low"], approved)
            if ok and not high and not s["allow_auto_low"] and not human_ok:
                ok, why = False, "low-risk auto-execution not enabled for this schedule"
            if ok and high and not human_ok: ok, why = False, "high-risk change requires human approval"
            if not ok:
                if "never auto-modified" not in why and not q("SELECT 1 FROM approvals WHERE plan_id=? AND status IN('pending','rejected')", (pid,)):
                    x("INSERT INTO approvals(id,run_id,plan_id,risk,reason) VALUES(?,?,?,?,?)", (uid(), rid, pid, plan.get("risk"), why))
                log(rid, "approval", f"not executed {pid}: {why}"); continue
            res = timed(A["m3"].execute, pid, {"run_id": rid, "policy_level": lvl, "approved": True}, STEP_TIMEOUT_S)
            touched |= set(plan.get("files", []))
            if res.get("verified"): log(rid, "execute", f"verified {pid}", data=res)
            else: log(rid, "execute", f"verification FAILED {pid}; rollback={res.get('rollback_ref')}", "error", res); final = "completed_with_failures"
    except AdapterNotConfigured as e:
        log(rid, "blocked", str(e), "error"); final = "blocked"
        x("UPDATE runs SET error=? WHERE id=?", (str(e), rid))
    except Exception as e:
        log(rid, "failure", f"{type(e).__name__}: {e}", "error"); final = "failed"
        x("UPDATE runs SET error=? WHERE id=?", (str(e), rid))
    x("UPDATE runs SET status=?,finished_at=?,lease_until=NULL WHERE id=? AND status='running'", (final, iso(now()), rid))
    build_report(rid)

# ---------- reports ----------
def build_report(rid):
    run = q("SELECT * FROM runs WHERE id=?", (rid,))[0]; ev = q("SELECT * FROM events WHERE run_id=? ORDER BY id", (rid,))
    fi = q("SELECT * FROM findings WHERE run_id=?", (rid,)); ap = q("SELECT * FROM approvals WHERE run_id=?", (rid,))
    def stage(st): return [e for e in ev if e["stage"] == st]
    done = [json.loads(e["data"]) for e in stage("execute") if e["level"] == "info"]
    body = {
        "run": run, "repository": run["repo_id"],
        "completed_work": {
            "tasks_discovered": len(fi), "plans_generated": len(stage("plan")),
            "validated_executions": [d for d in done], "patches": [json.loads(e["data"]).get("patch_ref") for e in stage("prepare")]},
        "failed_or_blocked": [e for e in ev if e["level"] == "error"],
        "security_findings": [f for f in fi if f["category"] == "security"],
        "findings": fi,
        "pending_approvals": [a for a in ap if a["status"] == "pending"],
        "recommendations (NOT done)": [f"Triage suspected finding: {f['title']}" for f in fi if f["status"] == "suspected"]
                                       + [f"Approve/reject plan {a['plan_id']}" for a in ap if a["status"] == "pending"],
        "timeline": [{"ts": e["ts"], "stage": e["stage"], "level": e["level"], "message": e["message"]} for e in ev]}
    x("INSERT OR REPLACE INTO reports(id,run_id,created_at,body) VALUES(?,?,?,?)", (uid(), rid, iso(now()), json.dumps(body, default=str)))
    notify(rid, run["status"])

def notify(run_id, status):  # notification interface: override/replace with email/Slack
    print(f"[nightshift] run {run_id} finished: {status}")

# ---------- worker ----------
_stop = threading.Event()
def worker_loop():
    while not _stop.is_set():
        try:
            recover_expired(); enqueue_due()
            while (rid := claim_next()): run_pipeline(rid)
        except Exception as e: print("worker error", e)
        _stop.wait(TICK_S)
def start_worker(): threading.Thread(target=worker_loop, daemon=True, name="nightshift-worker").start()

# ---------- API ----------
try:
    from fastapi import FastAPI, HTTPException, Header
    from pydantic import BaseModel
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_):
        conn(); start_worker(); yield; _stop.set()
    app = FastAPI(title="FORGEX NightShift", lifespan=lifespan)
    P = "/api/v1/nightshift"

    class ScheduleIn(BaseModel):
        repo_id: str; task_type: str; kind: str; spec: dict = {}; tz: str = "UTC"
        window_start: Optional[str] = None; window_end: Optional[str] = None
        priority: int = 5; policy_level: int = 0; allow_auto_low: bool = False
    class RunIn(BaseModel): schedule_id: str
    class Decision(BaseModel): approve: bool

    def _v(fn, *a):
        try: return fn(*a)
        except (AssertionError, KeyError, ValueError) as e: raise HTTPException(400, str(e))

    @app.post(P + "/schedules")
    def c(b: ScheduleIn): return _v(create_schedule, b.model_dump())
    @app.get(P + "/schedules")
    def ls(): return [{**s, "spec": json.loads(s["spec"])} for s in q("SELECT * FROM schedules ORDER BY created_at DESC")]
    @app.patch(P + "/schedules/{sid}")
    def pt(sid: str, b: dict):
        r = _v(update_schedule, sid, b)
        if not r: raise HTTPException(404); return r
    @app.post(P + "/schedules/{sid}/pause")
    def pa(sid: str): return set_paused(sid, True) or HTTPException(404)
    @app.post(P + "/schedules/{sid}/resume")
    def re(sid: str): return set_paused(sid, False) or HTTPException(404)
    @app.post(P + "/runs", status_code=202)
    def mr(b: RunIn, x_user: str = Header("operator")):
        rid = _v(manual_run, b.schedule_id, x_user)
        if not rid: raise HTTPException(404)
        return {"run_id": rid, "status": "queued"}
    @app.get(P + "/runs")
    def lr(): return q("SELECT * FROM runs ORDER BY rowid DESC LIMIT 200")
    @app.get(P + "/runs/{rid}")
    def gr(rid: str):
        r = q("SELECT * FROM runs WHERE id=?", (rid,))
        if not r: raise HTTPException(404)
        return {**r[0], "events": q("SELECT * FROM events WHERE run_id=? ORDER BY id", (rid,)),
                "findings": q("SELECT * FROM findings WHERE run_id=?", (rid,)), "approvals": q("SELECT * FROM approvals WHERE run_id=?", (rid,))}
    @app.get(P + "/reports")
    def lrep(): return q("SELECT id,run_id,created_at FROM reports ORDER BY created_at DESC")
    @app.get(P + "/reports/{rid}")
    def grep_(rid: str):
        r = q("SELECT * FROM reports WHERE id=?", (rid,))
        if not r: raise HTTPException(404)
        return json.loads(r[0]["body"])
    @app.get(P + "/policies")
    def pol(): return [{"level": k, "description": v} for k, v in POLICIES.items()]
    @app.get(P + "/approvals")
    def apq(): return q("SELECT * FROM approvals WHERE status='pending'")
    @app.post(P + "/approvals/{aid}/decision")
    def dec(aid: str, b: Decision, x_user: str = Header("operator")):
        n = x("UPDATE approvals SET status=?,decided_by=?,decided_at=? WHERE id=? AND status='pending'",
              ("approved" if b.approve else "rejected", x_user, iso(now()), aid))
        if not n: raise HTTPException(404, "not pending")
        return {"ok": True}
except ImportError:
    app = None
