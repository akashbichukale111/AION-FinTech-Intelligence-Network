import sqlite3, json, os, hashlib, threading, uuid
from datetime import datetime, timezone
_lock = threading.RLock()
GENESIS = "0" * 64

def db_path(): return os.environ.get("FORGEX_M10_DB", "forgex_m10.db")
def now(): return datetime.now(timezone.utc).isoformat()
def canon(o): return json.dumps(o, sort_keys=True, separators=(",", ":"), default=str)
def sha(s): return hashlib.sha256(s.encode() if isinstance(s, str) else s).hexdigest()
def nid(p): return f"{p}_{uuid.uuid4().hex[:12]}"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, name TEXT, role TEXT, kind TEXT DEFAULT 'human', key_hash TEXT UNIQUE, active INT DEFAULT 1);
CREATE TABLE IF NOT EXISTS policies(version INTEGER PRIMARY KEY, body TEXT, created_by TEXT, created_at TEXT, active INT);
CREATE TABLE IF NOT EXISTS releases(id TEXT PRIMARY KEY, version TEXT, commit_sha TEXT, base_sha TEXT, title TEXT, description TEXT, repo TEXT,
  author TEXT, agent_id TEXT, created_by TEXT, submitted_by TEXT, created_at TEXT, status TEXT, is_financial INT, diff_sha256 TEXT,
  changed_files TEXT, lines_added INT, lines_removed INT, cycle INT DEFAULT 0, pr_ref TEXT, artifact_ref TEXT, outcome TEXT, policy_version INT);
CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, release_id TEXT, module TEXT, kind TEXT, payload TEXT, source_ref TEXT, collector TEXT, commit_sha TEXT, content_hash TEXT, collected_at TEXT);
CREATE TABLE IF NOT EXISTS assessments(id TEXT PRIMARY KEY, release_id TEXT, created_at TEXT, overall TEXT, confidence REAL, body TEXT);
CREATE TABLE IF NOT EXISTS verifications(id TEXT PRIMARY KEY, release_id TEXT, created_at TEXT, body TEXT);
CREATE TABLE IF NOT EXISTS gate_evals(id TEXT PRIMARY KEY, release_id TEXT, created_at TEXT, status TEXT, policy_version INT, body TEXT);
CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, release_id TEXT, cycle INT, kind TEXT, decision TEXT, actor TEXT, role TEXT, commit_sha TEXT, policy_version INT, comment TEXT, created_at TEXT, expires_at TEXT);
CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, actor_kind TEXT, agent_id TEXT, action TEXT, release_id TEXT,
  prev_state TEXT, new_state TEXT, policy_version INT, evidence_refs TEXT, result TEXT, decision TEXT, details TEXT, prev_hash TEXT, hash TEXT);
"""

def connect():
    c = sqlite3.connect(db_path(), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c

def init():
    c = connect(); c.executescript(SCHEMA); c.commit(); c.close()

def q(sql, a=()):
    c = connect()
    try: return [dict(r) for r in c.execute(sql, a).fetchall()]
    finally: c.close()

def q1(sql, a=()):
    r = q(sql, a); return r[0] if r else None

def x(sql, a=()):
    c = connect()
    try:
        cur = c.execute(sql, a); c.commit(); return cur.lastrowid
    finally: c.close()

# ---------- tamper-evident audit (hash chain + external anchor file) ----------
AUDIT_FIELDS = ["ts","actor","actor_kind","agent_id","action","release_id","prev_state","new_state","policy_version","evidence_refs","result","decision","details"]

def audit(actor, action, release_id=None, prev_state=None, new_state=None, evidence_refs=None,
          result=None, decision=None, details=None, policy_version=None, agent_id=None):
    rec = dict(ts=now(), actor=actor["id"], actor_kind=actor.get("kind", "human"), agent_id=agent_id or (actor["id"] if actor.get("kind") == "agent" else None),
               action=action, release_id=release_id, prev_state=prev_state, new_state=new_state, policy_version=policy_version,
               evidence_refs=canon(evidence_refs or []), result=result, decision=decision, details=canon(details or {}))
    with _lock:
        c = connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            last = c.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
            prev = last["hash"] if last else GENESIS
            h = sha(prev + canon(rec))
            cur = c.execute(f"INSERT INTO audit({','.join(AUDIT_FIELDS)},prev_hash,hash) VALUES({','.join('?'*len(AUDIT_FIELDS))},?,?)",
                            [rec[f] for f in AUDIT_FIELDS] + [prev, h])
            c.commit()
            open(db_path() + ".anchor", "w").write(f"{cur.lastrowid}:{h}")
            return cur.lastrowid
        finally: c.close()

def audit_verify():
    rows = q("SELECT * FROM audit ORDER BY seq")
    prev, problems = GENESIS, []
    for r in rows:
        rec = {f: r[f] for f in AUDIT_FIELDS}
        if r["prev_hash"] != prev: problems.append({"seq": r["seq"], "problem": "chain link broken (record removed/reordered)"})
        if sha(r["prev_hash"] + canon(rec)) != r["hash"]: problems.append({"seq": r["seq"], "problem": "record content does not match its hash"})
        prev = r["hash"]
    anchor = None
    try:
        s, h = open(db_path() + ".anchor").read().split(":"); anchor = (int(s), h)
    except Exception: pass
    if anchor:
        if not rows or rows[-1]["seq"] < anchor[0]: problems.append({"seq": anchor[0], "problem": "tail truncated vs external anchor"})
        else:
            m = [r for r in rows if r["seq"] == anchor[0]]
            if m and m[0]["hash"] != anchor[1]: problems.append({"seq": anchor[0], "problem": "anchor hash mismatch (chain rewritten)"})
    return {"valid": not problems, "records": len(rows), "head_hash": prev, "problems": problems, "anchor_checked": bool(anchor),
            "limitation": "Tamper-EVIDENT, not immutable: someone with write access to both the DB and the anchor file can rewrite history consistently. "
                          "Export head_hash to an external system (WORM storage / separate log) for stronger guarantees."}
