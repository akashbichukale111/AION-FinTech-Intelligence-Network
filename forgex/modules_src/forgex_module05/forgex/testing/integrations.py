"""Integration contracts with FORGEX Modules 01-04 (+ Module 10 hook).

Modules 01-04 are not rewritten. Module 05 talks to them through four small
Protocols. The Local* defaults below make Module 05 runnable on its own;
FORGEX wires its real services with `providers.configure(...)` at startup.
"""
from __future__ import annotations
import os, shutil, subprocess, tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Protocol

from . import store


@dataclass
class RepoRef:                      # Module 01: repository structure + source evidence
    id: str
    path: str
    commit_ref: str = "unknown"
    name: str = ""

@dataclass
class Workspace:                    # Module 03: isolated workspace + diffs
    path: str
    changes: list = field(default_factory=list)   # [{path, change_type, before, after}]
    cleanup: Optional[Callable[[], None]] = None

class RepositoryProvider(Protocol):
    def resolve(self, repository_id: str | None, repo_path: str | None = None) -> RepoRef: ...
class PlanProvider(Protocol):       # Module 02: approved engineering plan -> affected files
    def affected_files(self, plan_id: str) -> list[str]: ...
class WorkspaceProvider(Protocol):  # Module 03
    def create(self, repo: RepoRef, change_set_id: str | None = None) -> Workspace: ...
class FindingsProvider(Protocol):   # security findings from available modules
    def findings(self, repository_id: str, commit_ref: str) -> list[dict]: ...


def _git(path, *args):
    try:
        r = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=30)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None

def git_changes(repo_path: str, base_ref: str) -> list[dict]:
    """Changes between base_ref and the working tree, with before/after contents."""
    out = _git(repo_path, "diff", "--name-status", base_ref) or ""
    changes = []
    for line in out.splitlines():
        parts = line.split("\t")
        st, p = parts[0][0], parts[-1]
        before = _git(repo_path, "show", f"{base_ref}:{parts[1] if st == 'R' else p}") if st in "MDR" else None
        fp = Path(repo_path) / p
        after = fp.read_text(errors="replace") if st != "D" and fp.is_file() else None
        changes.append({"path": p, "change_type": {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}.get(st, "modified"),
                        "before": before, "after": after})
    return changes


class LocalRepositoryProvider:
    def resolve(self, repository_id, repo_path=None):
        if repo_path:
            p = str(Path(repo_path).resolve())
            if not Path(p).is_dir():
                raise ValueError(f"repo_path not found: {repo_path}")
            rid = repository_id or Path(p).name
            ref = (_git(p, "rev-parse", "HEAD") or "unversioned").strip()
            store.put("repo", rid, {"id": rid, "path": p, "name": Path(p).name}, rid)
            return RepoRef(rid, p, ref, Path(p).name)
        doc = store.get("repo", repository_id or "")
        if not doc:
            raise ValueError("unknown repository_id; pass repo_path once to register it (or wire Module 01)")
        ref = (_git(doc["path"], "rev-parse", "HEAD") or "unversioned").strip()
        return RepoRef(doc["id"], doc["path"], ref, doc.get("name", ""))

class LocalPlanProvider:
    def affected_files(self, plan_id): return []

class LocalWorkspaceProvider:
    """Default isolated workspace = filtered copy of the repo (no .git, no escaping symlinks)."""
    MAX_MB = int(os.environ.get("FORGEX_TESTING_MAX_COPY_MB", "1024"))
    def create(self, repo, change_set_id=None):
        total = 0
        for dp, dn, fn in os.walk(repo.path):
            dn[:] = [d for d in dn if d != ".git"]
            total += sum(os.path.getsize(os.path.join(dp, f)) for f in fn if os.path.isfile(os.path.join(dp, f)))
        if total > self.MAX_MB * 1024 * 1024:
            raise ValueError(f"repository larger than {self.MAX_MB} MB; refusing to copy")
        root = tempfile.mkdtemp(prefix="forgex_ws_")
        ws = os.path.join(root, "work")
        shutil.copytree(repo.path, ws, symlinks=True, ignore=shutil.ignore_patterns(".git", "*.pyc", "__pycache__"))
        for dp, dn, fn in os.walk(ws):          # drop symlinks that point outside the workspace
            for n in dn + fn:
                p = os.path.join(dp, n)
                if os.path.islink(p) and not os.path.realpath(p).startswith(os.path.realpath(ws) + os.sep):
                    os.unlink(p)
        return Workspace(ws, [], lambda: shutil.rmtree(root, ignore_errors=True))

class NoFindings:
    def findings(self, repository_id, commit_ref): return []

class Providers:
    repository: RepositoryProvider = LocalRepositoryProvider()
    plan: PlanProvider = LocalPlanProvider()
    workspace: WorkspaceProvider = LocalWorkspaceProvider()
    security: FindingsProvider = NoFindings()
    def configure(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

providers = Providers()
