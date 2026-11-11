import json, os, sqlite3, threading

_local = threading.local()
SCHEMA = """
CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY, ts REAL NOT NULL, service TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL, trace_id TEXT, request_id TEXT, attrs TEXT, source TEXT NOT NULL, h TEXT UNIQUE);
CREATE INDEX IF NOT EXISTS ix_logs_ts ON logs(ts); CREATE INDEX IF NOT EXISTS ix_logs_trace ON logs(trace_id);
CREATE TABLE IF NOT EXISTS metrics(id INTEGER PRIMARY KEY, ts REAL NOT NULL, service TEXT NOT NULL, name TEXT NOT NULL, value REAL NOT NULL, labels TEXT, source TEXT NOT NULL, h TEXT UNIQUE);
CREATE INDEX IF NOT EXISTS ix_metrics_ts ON metrics(ts);
CREATE TABLE IF NOT EXISTS spans(id INTEGER PRIMARY KEY, ts REAL NOT NULL, trace_id TEXT NOT NULL, span_id TEXT NOT NULL, parent_id TEXT, service TEXT NOT NULL, name TEXT, dur_ms REAL NOT NULL, status TEXT NOT NULL, attrs TEXT, source TEXT NOT NULL, h TEXT UNIQUE);
CREATE INDEX IF NOT EXISTS ix_spans_ts ON spans(ts); CREATE INDEX IF NOT EXISTS ix_spans_trace ON spans(trace_id);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL, service TEXT NOT NULL, payload TEXT, source TEXT NOT NULL, h TEXT UNIQUE);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS incidents(id TEXT PRIMARY KEY, title TEXT, severity TEXT, status TEXT, services TEXT, owner TEXT, created_at REAL, updated_at REAL, started_at REAL, resolved_at REAL, closed_at REAL, origin TEXT, source TEXT, fingerprint TEXT, summary TEXT, pir TEXT, meta TEXT);
CREATE TABLE IF NOT EXISTS incident_log(id INTEGER PRIMARY KEY, incident_id TEXT, ts REAL, kind TEXT, actor TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS ix_ilog ON incident_log(incident_id);
CREATE TABLE IF NOT EXISTS hypotheses(id TEXT PRIMARY KEY, incident_id TEXT, generated_at REAL, body TEXT);
CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, incident_id TEXT, created_at REAL, status TEXT, body TEXT);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts REAL, actor TEXT, action TEXT, target TEXT, detail TEXT);
"""

def conn():
    path = os.getenv("M09_DB", "forgex_m09.db")
    if getattr(_local, "path", None) != path:
        c = sqlite3.connect(path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA synchronous=NORMAL")
        c.executescript(SCHEMA); c.commit()
        _local.c, _local.path = c, path
    return _local.c

def q(sql, a=()): return [dict(r) for r in conn().execute(sql, a).fetchall()]
def one(sql, a=()):
    r = q(sql, a); return r[0] if r else None
def x(sql, a=()):
    c = conn(); cur = c.execute(sql, a); c.commit(); return cur
def bulk(sql, rows):
    c = conn(); before = c.total_changes; c.executemany(sql, rows); c.commit(); return c.total_changes - before
def j(o): return json.dumps(o, separators=(",", ":"), default=str)
def lj(s, d=None):
    try: return json.loads(s) if s else d
    except ValueError: return d
