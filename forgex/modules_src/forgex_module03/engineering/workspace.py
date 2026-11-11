"""Isolated workspace, diff/patch engine, safe apply + rollback."""
import difflib, hashlib, os, re, shutil
from pathlib import Path

ROOT = Path(os.environ.get("FORGEX_WORK_ROOT", ".forgex_work")).resolve()
SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".forgex_work", ".forgex_backups"}
SECRET = re.compile(r"(^\.env($|\.)|\.pem$|\.key$|id_rsa|credentials|secrets?\.(json|ya?ml)$)", re.I)

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def create(repo, eid):
    """Copy repo (minus secrets/vendor dirs) into an isolated workspace. Original is never touched."""
    src = Path(repo).resolve()
    if not src.is_dir(): raise ValueError("repository path not found")
    ws = ROOT / eid / "ws"; manifest = {}
    for dp, dn, fn in os.walk(src):
        dn[:] = [d for d in dn if d not in SKIP]
        for f in fn:
            s = Path(dp) / f
            if SECRET.search(f) or s.is_symlink() or s.stat().st_size > 2_000_000: continue
            rel = s.relative_to(src).as_posix(); d = ws / rel
            d.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(s, d); manifest[rel] = sha(s)
    return str(ws), manifest

def safe_path(ws, rel):
    base = Path(ws).resolve(); p = (base / rel).resolve()
    if not p.is_relative_to(base) or SECRET.search(p.name) or ".git" in p.parts:
        raise PermissionError(f"path blocked: {rel}")
    return p

def _read(p): return Path(p).read_text(errors="replace") if Path(p).exists() else ""

def compute_changes(repo, ws, manifest, meta):
    """Before/after diff for every changed or new file. meta: rel -> {step_id, agent, explanation, evidence}."""
    changes, wsp = [], Path(ws)
    for dp, dn, fn in os.walk(wsp):
        for f in fn:
            p = Path(dp) / f; rel = p.relative_to(wsp).as_posix()
            if rel in manifest and sha(p) == manifest[rel]: continue
            before = _read(Path(repo) / rel) if rel in manifest else ""
            after = _read(p)
            diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), f"a/{rel}", f"b/{rel}"))
            m = meta.get(rel, {})
            changes.append({"file": rel, "status": "modified" if rel in manifest else "added", "diff": diff,
                            "additions": sum(1 for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")),
                            "deletions": sum(1 for l in diff.splitlines() if l.startswith("-") and not l.startswith("---")),
                            "after_sha": sha(p), "step_id": m.get("step_id"), "agent": m.get("agent"),
                            "explanation": m.get("explanation"), "evidence": m.get("evidence", [])})
    return sorted(changes, key=lambda c: c["file"])

def export_patch(changes):
    text = "".join(c["diff"] for c in changes)
    return text, hashlib.sha256(text.encode()).hexdigest()

def apply_to_repo(repo, ws, manifest, changes, eid):
    """Human-approved apply: conflict check -> backup -> write. Raises on conflict before touching anything."""
    repo_p = Path(repo).resolve()
    for c in changes:
        t = repo_p / c["file"]
        if c["status"] == "modified" and (not t.exists() or sha(t) != manifest[c["file"]]):
            raise RuntimeError(f"conflict: {c['file']} changed in the original repo since execution started")
        if c["status"] == "added" and t.exists():
            raise RuntimeError(f"conflict: {c['file']} now exists in the original repo")
    bk = repo_p / ".forgex_backups" / eid
    for c in changes:
        t = repo_p / c["file"]
        if c["status"] == "modified":
            (bk / c["file"]).parent.mkdir(parents=True, exist_ok=True); shutil.copy2(t, bk / c["file"])
        t.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(Path(ws) / c["file"], t)

def rollback(repo, changes, eid):
    repo_p = Path(repo).resolve(); bk = repo_p / ".forgex_backups" / eid
    if not bk.exists() and any(c["status"] == "modified" for c in changes): raise RuntimeError("backup missing; cannot roll back")
    for c in changes:
        t = repo_p / c["file"]
        if c["status"] == "added":
            if t.exists(): t.unlink()
        else: shutil.copy2(bk / c["file"], t)
