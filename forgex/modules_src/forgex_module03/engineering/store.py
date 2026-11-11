"""Persistent execution + audit store (SQLite, stdlib only)."""
import json, os, sqlite3, threading, time

DB = os.environ.get("FORGEX_ENG_DB", "forgex_engineering.db")
_lock = threading.Lock()

def _c():
    c = sqlite3.connect(DB, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c

def init():
    with _lock, _c() as c:
        c.execute("create table if not exists executions(id text primary key, plan_id text, repo text, status text, data text, created real, updated real)")
        c.execute("create table if not exists audit(id integer primary key autoincrement, execution_id text, ts real, agent text, event text, detail text)")

def save(ex):
    ex["updated"] = time.time()
    with _lock, _c() as c:
        c.execute("insert or replace into executions values(?,?,?,?,?,?,?)",
                  (ex["id"], ex["plan_id"], ex["repo"], ex["status"], json.dumps(ex), ex["created"], ex["updated"]))

def get(eid):
    with _lock, _c() as c:
        r = c.execute("select data from executions where id=?", (eid,)).fetchone()
    return json.loads(r["data"]) if r else None

def list_all():
    with _lock, _c() as c:
        rows = c.execute("select data from executions order by created desc").fetchall()
    out = []
    for r in rows:
        d = json.loads(r["data"]); d.pop("changes", None); out.append(d)
    return out

def audit(eid, agent, event, detail=""):
    with _lock, _c() as c:
        c.execute("insert into audit(execution_id,ts,agent,event,detail) values(?,?,?,?,?)", (eid, time.time(), agent, event, str(detail)[:2000]))

def audit_log(eid):
    with _lock, _c() as c:
        return [dict(r) for r in c.execute("select ts,agent,event,detail from audit where execution_id=? order by id", (eid,))]
