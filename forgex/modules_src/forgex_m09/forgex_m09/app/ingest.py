"""Telemetry ingestion adapters: JSON/text logs, JSON/Prometheus metrics, simple/OTLP-JSON traces, deploy/health/alert events.
Everything is validated, redacted, de-duplicated (hash UNIQUE) and size-bounded. Nothing is fabricated."""
import hashlib, json, math, re, time
from datetime import datetime, timezone
from .config import FUTURE_SKEW_S, MAX_AGE_DAYS, MAX_BATCH, RETENTION_ROWS
from .db import bulk, one, x
from .security import clean, redact_obj, safe_text

SVC_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,63}$")
HEX_RE = re.compile(r"^[0-9a-f]{16,32}$")
SPAN_RE = re.compile(r"^[0-9a-f]{8,32}$")
NAME_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]{0,127}$")
LEVELS = {"TRACE": "DEBUG", "DEBUG": "DEBUG", "INFO": "INFO", "NOTICE": "INFO", "WARN": "WARN", "WARNING": "WARN",
          "ERROR": "ERROR", "ERR": "ERROR", "CRITICAL": "FATAL", "FATAL": "FATAL"}
EVENT_KINDS = {"deploy", "health", "alert", "incident"}
SOURCES = {"real", "imported", "synthetic"}

class Rej(Exception): pass

def parse_ts(v, now, hist=False):
    if v is None or v == "" or isinstance(v, bool): raise Rej("missing timestamp")
    try:
        if isinstance(v, (int, float)) or re.fullmatch(r"-?\d+(\.\d+)?", str(v).strip()):
            f = float(v)
            for div, lim in ((1e9, 1e17), (1e6, 1e14), (1e3, 1e11)):   # ns, us, ms -> s
                if abs(f) >= lim: f /= div; break
        else:
            d = datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
            f = (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    except Exception: raise Rej(f"unparseable timestamp: {clean(v, 40)}")
    if not math.isfinite(f): raise Rej("non-finite timestamp")
    if f > now + FUTURE_SKEW_S: raise Rej("timestamp in the future")
    if not hist and f < now - MAX_AGE_DAYS * 86400: raise Rej("timestamp older than retention window")
    return f

def svc(v):
    if not isinstance(v, str): raise Rej("missing service id")
    s = v.strip().lower()
    if not SVC_RE.match(s): raise Rej(f"invalid service id: {clean(v, 40)}")
    return s

def _tid(v):
    v = str(v).strip().lower() if v else ""
    return v if HEX_RE.match(v) else None   # invalid/missing -> None (never guessed)

def _h(*parts): return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()
def _attrs_json(a):
    s = json.dumps(redact_obj(a), separators=(",", ":"), default=str)
    return s if len(s) <= 8192 else '{"_truncated":true}'

def _g(d, *ks): return next((d[k] for k in ks if k in d and d[k] is not None), None)

def _prune(t):
    m = one(f"SELECT MAX(id) m FROM {t}")
    if m and m["m"] and m["m"] > RETENTION_ROWS: x(f"DELETE FROM {t} WHERE id<=?", (m["m"] - RETENTION_ROWS,))

def _batch(kind, sql, items, parse, source, hist, **kw):
    if source not in SOURCES: raise ValueError("source must be real|imported|synthetic")
    if not isinstance(items, list): raise ValueError("records must be a list")
    if len(items) > MAX_BATCH: raise ValueError(f"batch too large (max {MAX_BATCH})")
    now, rows, errs, rej = time.time(), [], [], 0
    for i, it in enumerate(items):
        try: rows.append(parse(it, now, hist, source, **kw))
        except Rej as e:
            rej += 1
            if len(errs) < 20: errs.append({"index": i, "error": str(e)})
        except Exception:
            rej += 1
            if len(errs) < 20: errs.append({"index": i, "error": "malformed record"})
    ins = bulk(sql, rows) if rows else 0
    _prune(kind)
    return {"accepted": ins, "duplicates": len(rows) - ins, "rejected": rej, "errors": errs, "source": source}

# ---------------- logs ----------------
LOG_LINE = re.compile(r"^(?P<ts>\S+)\s+(?P<lvl>[A-Za-z]+)\s+(?:\[(?P<svc>[^\]]+)\]\s+)?(?P<msg>.*)$")
TRACE_IN_MSG = re.compile(r"trace[_.-]?id[=: ]+([0-9a-fA-F]{16,32})")
_LOG_KNOWN = {"timestamp", "ts", "time", "@timestamp", "level", "severity", "lvl", "levelname", "message", "msg", "log", "event",
              "service", "service.name", "service_name", "app", "trace_id", "traceId", "trace.id", "request_id", "requestId", "req_id"}

def parse_log(rec, now, hist, source, default_service=None):
    if isinstance(rec, str):
        m = LOG_LINE.match(rec)
        if not m: raise Rej("unparseable log line")
        t = TRACE_IN_MSG.search(rec)
        d = {"ts": m["ts"], "level": m["lvl"], "service": m["svc"] or default_service, "message": m["msg"], "trace_id": t.group(1) if t else None}
    elif isinstance(rec, dict): d = rec
    else: raise Rej("record must be an object or a line")
    ts = parse_ts(_g(d, "timestamp", "ts", "time", "@timestamp"), now, hist)
    service = svc(_g(d, "service", "service.name", "service_name", "app") or default_service)
    lvl = LEVELS.get(str(_g(d, "level", "severity", "lvl", "levelname") or "INFO").upper())
    if not lvl: raise Rej("invalid level")
    msg = _g(d, "message", "msg", "log", "event")
    if msg is None: raise Rej("missing message")
    msg = safe_text(msg)
    tid = _tid(_g(d, "trace_id", "traceId", "trace.id"))
    rid = _g(d, "request_id", "requestId", "req_id"); rid = safe_text(rid, 64) if rid else None
    attrs = _attrs_json({k: v for k, v in d.items() if k not in _LOG_KNOWN})
    return (ts, service, lvl, msg, tid, rid, attrs, source, _h(ts, service, lvl, msg, tid, rid, _g(d, "id", "event_id", "seq")))

def ingest_logs(p, source="real", hist=False):
    p = {"records": p} if isinstance(p, list) else (p or {})
    items = list(p.get("records") or []) + list(p.get("lines") or [])
    return _batch("logs", "INSERT OR IGNORE INTO logs(ts,service,level,message,trace_id,request_id,attrs,source,h) VALUES(?,?,?,?,?,?,?,?,?)",
                  items, parse_log, source, hist, default_service=p.get("service"))

# ---------------- metrics ----------------
PROM = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+(\S+)(?:\s+(-?\d+))?\s*$')
LBL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')

def parse_metric(d, now, hist, source, default_service=None):
    if not isinstance(d, dict): raise Rej("record must be an object")
    name = d.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name): raise Rej("invalid metric name")
    try: val = float(d.get("value"))
    except (TypeError, ValueError): raise Rej("non-numeric value")
    if not math.isfinite(val): raise Rej("non-finite value")
    labels = d.get("labels") or {}
    if not isinstance(labels, dict): raise Rej("labels must be an object")
    service = svc(d.get("service") or labels.get("service") or labels.get("job") or default_service)
    ts = parse_ts(d.get("ts", d.get("timestamp")), now, hist)
    lj = json.dumps(redact_obj(labels), sort_keys=True, separators=(",", ":"))[:2048]
    return (ts, service, name, val, lj, source, _h(ts, service, name, lj, val))

def prom_to_records(text, now):
    recs, bad = [], 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"): continue
        m = PROM.match(line)
        if not m: bad += 1; continue
        labels = {k: v for k, v in LBL.findall(m[2] or "")}
        recs.append({"name": m[1], "labels": labels, "value": m[3] if m[3].lower() not in ("nan", "+inf", "-inf", "inf") else None,
                     "ts": int(m[4]) if m[4] else now})
    return recs, bad

def ingest_metrics(p, source="real", hist=False):
    p = {"records": p} if isinstance(p, list) else (p or {})
    items, warn = list(p.get("records") or []), []
    if p.get("text"):
        if not isinstance(p["text"], str) or len(p["text"]) > 4_000_000: raise ValueError("prometheus text too large")
        recs, bad = prom_to_records(p["text"], time.time()); items += recs
        if bad: warn.append(f"{bad} unparseable prometheus lines skipped")
        if any(not isinstance(r["ts"], int) for r in recs): warn.append("samples without timestamps were stamped with ingestion time")
    r = _batch("metrics", "INSERT OR IGNORE INTO metrics(ts,service,name,value,labels,source,h) VALUES(?,?,?,?,?,?,?)",
               items, parse_metric, source, hist, default_service=p.get("service"))
    r["warnings"] = warn; return r

# ---------------- traces ----------------
def _otlp_attrs(lst):
    out = {}
    for a in lst or []:
        v = a.get("value") or {}
        v = next(iter(v.values()), None) if isinstance(v, dict) else v
        if isinstance(v, str) and re.fullmatch(r"-?\d+", v): v = int(v)
        out[str(a.get("key"))] = v
    return out

def flatten_otlp(p):
    out = []
    for rs in p.get("resourceSpans") or []:
        ra = _otlp_attrs((rs.get("resource") or {}).get("attributes"))
        for ss in rs.get("scopeSpans") or rs.get("instrumentationLibrarySpans") or []:
            for s in ss.get("spans") or []:
                try:
                    st, en = int(s["startTimeUnixNano"]), int(s.get("endTimeUnixNano", s["startTimeUnixNano"]))
                    dur = (en - st) / 1e6
                except Exception: st, dur = None, None
                code = (s.get("status") or {}).get("code")
                out.append({"trace_id": s.get("traceId"), "span_id": s.get("spanId"), "parent_id": s.get("parentSpanId") or None,
                            "service": ra.get("service.name"), "name": s.get("name"), "start": st, "duration_ms": dur,
                            "status": "error" if code in (2, "STATUS_CODE_ERROR") else "ok", "attrs": _otlp_attrs(s.get("attributes"))})
    return out

def parse_span(d, now, hist, source):
    if not isinstance(d, dict): raise Rej("record must be an object")
    tid = _tid(d.get("trace_id"))
    if not tid: raise Rej("invalid or missing trace_id")
    sid = str(d.get("span_id") or "").lower()
    if not SPAN_RE.match(sid): raise Rej("invalid or missing span_id")
    pid = str(d.get("parent_id") or d.get("parent_span_id") or "").lower()
    pid = pid if SPAN_RE.match(pid) and set(pid) != {"0"} else None
    ts = parse_ts(_g(d, "start", "ts", "timestamp"), now, hist)
    try: dur = float(d.get("duration_ms"))
    except (TypeError, ValueError): raise Rej("invalid duration_ms")
    if not (0 <= dur <= 86_400_000): raise Rej("duration out of range")
    st = "error" if str(d.get("status", "ok")).lower() in ("error", "2", "fail", "failed") else "ok"
    return (ts, tid, sid, pid, svc(d.get("service")), safe_text(d.get("name") or "", 128), dur, st,
            _attrs_json(d.get("attrs") or {}), source, _h(tid, sid))

def ingest_traces(p, source="real", hist=False):
    p = {"spans": p} if isinstance(p, list) else (p or {})
    items = list(p.get("spans") or []) + (flatten_otlp(p) if p.get("resourceSpans") else [])
    return _batch("spans", "INSERT OR IGNORE INTO spans(ts,trace_id,span_id,parent_id,service,name,dur_ms,status,attrs,source,h) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  items, parse_span, source, hist)

# ---------------- events (deploy / health / alert) ----------------
def parse_event(d, now, hist, source):
    if not isinstance(d, dict): raise Rej("record must be an object")
    kind = str(d.get("kind", "")).lower()
    if kind not in EVENT_KINDS: raise Rej(f"kind must be one of {sorted(EVENT_KINDS)}")
    ts = parse_ts(_g(d, "ts", "timestamp"), now, hist); service = svc(d.get("service"))
    pl = json.dumps(redact_obj(d.get("payload") or {}), separators=(",", ":"), default=str)[:4096]
    return (ts, kind, service, pl, source, _h(kind, ts, service, pl))

def ingest_events(p, source="real", hist=False):
    p = {"records": p} if isinstance(p, list) else (p or {})
    return _batch("events", "INSERT OR IGNORE INTO events(ts,kind,service,payload,source,h) VALUES(?,?,?,?,?,?)",
                  list(p.get("records") or []), parse_event, source, hist)
