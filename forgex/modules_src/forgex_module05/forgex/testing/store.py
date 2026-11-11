"""Persistence: one SQLite doc-store (kind, id, repo, created_at, body JSON).
Swap for FORGEX's existing DB layer by re-implementing these 5 functions."""
from __future__ import annotations
import json, os, sqlite3, threading, uuid
from contextlib import closing
from datetime import datetime, timezone

_lock = threading.RLock()

def _c():
    c = sqlite3.connect(os.environ.get("FORGEX_TESTING_DB", "forgex_testing.db"), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE IF NOT EXISTS docs(kind TEXT, id TEXT, repo TEXT, created_at TEXT, body TEXT, PRIMARY KEY(kind,id))")
    c.execute("CREATE INDEX IF NOT EXISTS ix_docs ON docs(kind, repo, created_at)")
    return c

def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"

def put(kind, id_, body, repo=None):
    with _lock, closing(_c()) as c:
        old = c.execute("SELECT created_at FROM docs WHERE kind=? AND id=?", (kind, id_)).fetchone()
        c.execute("INSERT OR REPLACE INTO docs VALUES(?,?,?,?,?)",
                  (kind, id_, repo, old["created_at"] if old else now(), json.dumps(body, default=str)))
        c.commit()
    return body

def get(kind, id_):
    with _lock, closing(_c()) as c:
        r = c.execute("SELECT body FROM docs WHERE kind=? AND id=?", (kind, id_)).fetchone()
    return json.loads(r["body"]) if r else None

def update(kind, id_, patch: dict):
    with _lock:
        cur = get(kind, id_) or {}
        cur.update(patch)
        with closing(_c()) as c:
            r = c.execute("SELECT repo FROM docs WHERE kind=? AND id=?", (kind, id_)).fetchone()
        return put(kind, id_, cur, r["repo"] if r else cur.get("repository_id"))

def listing(kind, repo=None, limit=200):
    q, a = "SELECT body FROM docs WHERE kind=?", [kind]
    if repo:
        q += " AND repo=?"; a.append(repo)
    q += " ORDER BY created_at DESC LIMIT ?"; a.append(limit)
    with _lock, closing(_c()) as c:
        return [json.loads(r["body"]) for r in c.execute(q, a).fetchall()]
