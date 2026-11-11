"""D. Failure intelligence: classify, group, attach evidence. Causes are 'likely' only when evidence supports them."""
from __future__ import annotations
import re
from collections import defaultdict

RULES = [
    ("timeout", r"timed out|TimeoutError|Timeout\b|deadline exceeded|exceeded .{0,30}timeout"),
    ("import", r"ModuleNotFoundError|ImportError|Cannot find module|ERR_MODULE_NOT_FOUND|ClassNotFoundException|NoClassDefFoundError|cannot find symbol|package [\w.]+ does not exist"),
    ("dependency", r"No matching distribution|VersionConflict|DistributionNotFound|Could not resolve dependencies|ERESOLVE|incompatible (version|with)|requires .{0,40} but .{0,40} installed"),
    ("configuration", r"fixture '[^']+' not found|ConfigurationError|ImproperlyConfigured|Missing required (env|config)|KeyError: '[A-Z][A-Z0-9_]{3,}'|invalid configuration|no such option"),
    ("environment", r"PermissionError|Permission denied|ConnectionRefused|ECONNREFUSED|Name or service not known|Temporary failure in name resolution|Network is unreachable|No space left|command not found|Read-only file system|ENOTFOUND|UnknownHostException"),
    ("assertion", r"AssertionError|^\s*assert\b|\bExpected\b.{0,80}\b(but|to)\b|expect\(.*\)\.|toBe\(|toEqual\(|ComparisonFailure|AssertionFailedError"),
    ("exception", r"Traceback|Exception|Error\b|\bat [\w.$<>]+\(.*:\d+(:\d+)?\)"),
]
INFRA = {"timeout", "import", "dependency", "configuration", "environment"}
FLAKY_HINTS = re.compile(r"random|race condition|sleep|retry|connection reset|socket|intermittent|order[- ]dependent|timing", re.I)
EXC = re.compile(r"\b([A-Z][A-Za-z0-9_.]*(?:Error|Exception|Failure|Timeout))\b")
PYFRAME = re.compile(r'File "([^"]+)", line (\d+), in (\S+)')
JSFRAME = re.compile(r"at (?:(\S+) )?\(?([^\s()]+):(\d+):\d+\)?")
JAVAFRAME = re.compile(r"at ([\w.$]+)\(([\w]+\.java):(\d+)\)")

def classify(text):
    for cat, rx in RULES:
        if re.search(rx, text, re.I | re.M): return cat
    return "unknown"

def _frames(text):
    fr = [(f, int(l), fn) for f, l, fn in PYFRAME.findall(text)]
    fr += [(f, int(l), fn or "?") for fn, f, l in JSFRAME.findall(text)]
    fr += [(f, int(l), c) for c, f, l in JAVAFRAME.findall(text)]
    return [x for x in fr if "site-packages" not in x[0] and "node_modules" not in x[0] and "/usr/lib" not in x[0]
            and not x[0].startswith("<")]

def _repo_rel(path, known):
    p = path.replace("\\", "/")
    m = [k for k in known if p.endswith(k)]
    return max(m, key=len) if m else None

def _norm_msg(s):
    s = re.sub(r"0x[0-9a-fA-F]+", "0x", s); s = re.sub(r"\d+", "N", s)
    return s.strip()[:140]

def analyze(results, known_files=(), changed_files=(), base_status=None, history=None, test_map=None):
    """results: list of test dicts. base_status: {name: status} from the comparison run.
    history: {name: set(statuses seen on the same commit)} for flaky detection."""
    base_status, history, test_map = base_status or {}, history or {}, test_map or {}
    failing = [r for r in results if r["status"] in ("failed", "error")]
    groups = defaultdict(list)
    meta = {}
    for r in failing:
        text = (r.get("message") or "") + "\n" + (r.get("details") or "")
        cat = classify(text)
        exc = (EXC.findall(text) or [None])[0]
        frames = _frames(text)
        top = frames[-1] if frames else None
        top_rel = _repo_rel(top[0], known_files) if top else None
        first = next((l for l in (r.get("message") or text).splitlines() if l.strip()), "")
        sig = (cat, exc, top_rel or (top[0] if top else None), top[2] if top else None, _norm_msg(first) if not exc or cat == "assertion" else "")
        groups[sig].append(r)
        meta[sig] = {"frame": top, "top_rel": top_rel, "first": first.strip()[:300]}
    out = []
    for i, (sig, rs) in enumerate(sorted(groups.items(), key=lambda kv: -len(kv[1])), 1):
        cat, exc, _, _, _ = sig
        m = meta[sig]
        evidence = []
        if m["first"]: evidence.append({"type": "message", "text": m["first"]})
        if m["frame"]: evidence.append({"type": "top_frame", "text": f"{m['frame'][0]}:{m['frame'][1]} in {m['frame'][2]}"})
        evidence.append({"type": "affected_tests", "text": f"{len(rs)} test(s) share this signature"})
        regressed = [r["name"] for r in rs if base_status.get(r["name"]) == "passed"]
        if regressed: evidence.append({"type": "previously_passing", "text": f"{len(regressed)} passed in the comparison run"})
        related = sorted({src for r in rs for src in test_map.get(r.get("file"), [])} & set(changed_files))
        in_changed = bool(m["top_rel"] and m["top_rel"] in set(changed_files)) or bool(related)
        culprit = m["top_rel"] if m["top_rel"] in set(changed_files) else (related[0] if related else m["top_rel"])
        if m["top_rel"] in set(changed_files): evidence.append({"type": "changed_file", "text": f"failing frame is in modified file {m['top_rel']}"})
        elif related: evidence.append({"type": "changed_file", "text": f"failing test(s) cover modified file {', '.join(related)}"})
        nature = "possible_infrastructure" if cat in INFRA else "actual_failure"
        mod = re.search(r"No module named '([\w.]+)'|Cannot find module '([^']+)'", " ".join(e["text"] for e in evidence))
        if cat == "import" and mod:
            name = (mod.group(1) or mod.group(2) or "").split(".")[0].split("/")[-1]
            if any(k.split("/")[-1].split(".")[0] == name for k in known_files):
                nature = "actual_failure"; evidence.append({"type": "repo_module", "text": f"'{name}' exists in the repository, so this is likely a code/packaging problem"})
        score = (1 if m["frame"] else 0) + (1 if exc else 0) + (2 if in_changed else 0) + (1 if regressed else 0)
        confidence = "high" if score >= 5 else "medium" if score >= 3 else "low"
        if in_changed and regressed:
            where = f" at {m['frame'][0].split('/')[-1]}:{m['frame'][1]}" if m["frame"] else ""
            cause = f"Likely caused by the change to {culprit}: tests that previously passed now fail{where}."
        elif cat in INFRA:
            cause = f"Looks like a {cat} problem rather than a product bug; confirm by re-running in a clean environment."
        elif m["frame"] and m["top_rel"]:
            cause = f"Failure surfaces in {m['top_rel']} ({m['frame'][2]}); the cause of the incorrect behaviour is not established."
        else:
            cause = None
        if cause is None or confidence == "low" and not in_changed and not regressed:
            cause = (cause + " " if cause else "") + "Insufficient evidence for a root cause."
        flaky = [r["name"] for r in rs if len(history.get(r["name"], set())) > 1]
        hints = bool(FLAKY_HINTS.search(" ".join(e["text"] for e in evidence)))
        out.append({"group_id": f"fg_{i}", "category": cat, "exception": exc, "nature": nature, "tests": [r["name"] for r in rs],
                    "count": len(rs), "likely_cause": cause, "confidence": confidence, "evidence": evidence,
                    "flaky_indicators": {"mixed_results_same_commit": flaky, "message_hints": hints}})
    return out
