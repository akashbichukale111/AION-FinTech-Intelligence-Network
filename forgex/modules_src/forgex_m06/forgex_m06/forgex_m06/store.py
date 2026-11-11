"""SQLite persistence: scenarios, runs, findings, traces, evidence registry, tasks."""
from __future__ import annotations
import json, sqlite3, threading
from datetime import datetime, timezone
from .money import jsonable, digest

SCHEMA = """
CREATE TABLE IF NOT EXISTS scenarios(scenario_id TEXT PRIMARY KEY, name TEXT, type TEXT, builtin INTEGER, config TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, scenario_id TEXT, name TEXT, type TEXT, status TEXT, seed INTEGER, digest TEXT,
  config TEXT, summary TEXT, result TEXT, triggered_by TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS findings(finding_id TEXT PRIMARY KEY, run_id TEXT, ftype TEXT, severity TEXT, body TEXT);
CREATE TABLE IF NOT EXISTS evidence(evidence_id TEXT PRIMARY KEY, run_id TEXT, kind TEXT, sha256 TEXT, size INTEGER, payload TEXT);
CREATE TABLE IF NOT EXISTS tasks(task_id TEXT PRIMARY KEY, finding_id TEXT, run_id TEXT, status TEXT, body TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS ix_runs_created ON runs(created_at);
CREATE INDEX IF NOT EXISTS ix_find_run ON findings(run_id);
CREATE INDEX IF NOT EXISTS ix_ev_run ON evidence(run_id);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path=":memory:"):
        self.path, self.lock = path, threading.RLock()
        self._mem = sqlite3.connect(path, check_same_thread=False) if path == ":memory:" else None
        with self.conn() as c:
            c.executescript(SCHEMA)

    def conn(self):
        c = self._mem or sqlite3.connect(self.path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    def q(self, sql, args=(), one=False):
        with self.lock:
            c = self.conn()
            rows = c.execute(sql, args).fetchall()
            if not self._mem:
                c.close()
            return (rows[0] if rows else None) if one else rows

    def x(self, sql, args=()):
        with self.lock:
            c = self.conn()
            cur = c.execute(sql, args)
            c.commit()
            if not self._mem:
                c.close()
            return cur.lastrowid

    # scenarios
    def put_scenario(self, sid, name, type_, cfg, builtin=False):
        self.x("INSERT OR REPLACE INTO scenarios VALUES(?,?,?,?,?,?)", (sid, name, type_, int(builtin), json.dumps(jsonable(cfg)), now()))

    def scenarios(self):
        return [dict(json.loads(r["config"]), scenario_id=r["scenario_id"], builtin=bool(r["builtin"]), created_at=r["created_at"]) for r in self.q("SELECT * FROM scenarios ORDER BY builtin DESC, scenario_id")]

    def scenario(self, sid):
        r = self.q("SELECT * FROM scenarios WHERE scenario_id=?", (sid,), True)
        return r and dict(json.loads(r["config"]), scenario_id=sid, builtin=bool(r["builtin"]))

    def count(self, table):
        return self.q(f"SELECT COUNT(*) n FROM {table}", one=True)["n"]

    # runs
    def next_run_id(self):
        return f"RUN-{self.count('runs') + 1:05d}"

    def save_run(self, run, findings, evidence):
        with self.lock:
            self.x("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (run["run_id"], run["scenario_id"], run["name"], run["type"], run["status"], run["seed"], run["digest"],
                   json.dumps(run["config"]), json.dumps(run["summary"]), json.dumps(run["result"]), run.get("triggered_by", "manual"), now()))
            for f in findings:
                self.x("INSERT INTO findings VALUES(?,?,?,?,?)", (f["finding_id"], run["run_id"], f["failure_type"], f["severity"], json.dumps(f)))
            for e in evidence:
                self.x("INSERT INTO evidence VALUES(?,?,?,?,?,?)", (e["evidence_id"], run["run_id"], e["kind"], e["sha256"], e["size"], json.dumps(e["payload"])))

    def runs(self, limit=100, offset=0, scenario_id=None):
        w, a = ("WHERE scenario_id=?", [scenario_id]) if scenario_id else ("", [])
        rows = self.q(f"SELECT run_id,scenario_id,name,type,status,seed,digest,summary,triggered_by,created_at FROM runs {w} ORDER BY created_at DESC, run_id DESC LIMIT ? OFFSET ?", a + [limit, offset])
        return [dict(r, summary=json.loads(r["summary"])) for r in rows], self.count("runs")

    def run(self, run_id):
        r = self.q("SELECT * FROM runs WHERE run_id=?", (run_id,), True)
        return r and dict(r, config=json.loads(r["config"]), summary=json.loads(r["summary"]), result=json.loads(r["result"]))

    def findings(self, run_id=None):
        rows = self.q("SELECT body FROM findings WHERE run_id=?" if run_id else "SELECT body FROM findings", (run_id,) if run_id else ())
        return [json.loads(r["body"]) for r in rows]

    def finding(self, fid):
        r = self.q("SELECT body FROM findings WHERE finding_id=?", (fid,), True)
        return r and json.loads(r["body"])

    def evidence(self, run_id, with_payload=False):
        rows = self.q("SELECT * FROM evidence WHERE run_id=? ORDER BY evidence_id", (run_id,))
        return [{"evidence_id": r["evidence_id"], "kind": r["kind"], "sha256": r["sha256"], "size": r["size"], **({"payload": json.loads(r["payload"])} if with_payload else {})} for r in rows]

    def evidence_one(self, eid):
        r = self.q("SELECT * FROM evidence WHERE evidence_id=?", (eid,), True)
        return r and {"evidence_id": eid, "run_id": r["run_id"], "kind": r["kind"], "sha256": r["sha256"], "payload": json.loads(r["payload"])}

    # tasks
    def save_task(self, t):
        self.x("INSERT OR REPLACE INTO tasks VALUES(?,?,?,?,?,?)", (t["task_id"], t["finding_id"], t["run_id"], t["status"], json.dumps(jsonable(t)), t.get("created_at") or now()))

    def tasks(self):
        return [json.loads(r["body"]) for r in self.q("SELECT body FROM tasks ORDER BY created_at DESC, task_id DESC")]

    def task(self, tid):
        r = self.q("SELECT body FROM tasks WHERE task_id=?", (tid,), True)
        return r and json.loads(r["body"])
