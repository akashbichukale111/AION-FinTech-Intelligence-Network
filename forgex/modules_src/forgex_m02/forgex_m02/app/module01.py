"""Module 01 integration boundary. Module 02 NEVER scans repositories; it only consumes Module 01 scan results.

Expected scan contract (adapt `normalize()` if your Module 01 output differs):
{ "repo_id": str, "scan_id": str, "scanned_at": ISO-8601,
  "files": [{"path","language","kind"(source|config|model|migration|schema|test),"is_test","imports":[path|module],"symbols":[str]}],
  "apis": [{"method","path","file","handler"}], "configs": [{"path","keys":[str]}],
  "dependencies": [{"name","version","manifest"}], "allowed_users": [optional ACL] }
"""
import json, os
from abc import ABC, abstractmethod
from pathlib import Path

class Module01Client(ABC):
    @abstractmethod
    def list_repositories(self, actor: str) -> list[dict]: ...
    @abstractmethod
    def get_scan(self, repo_id: str, actor: str) -> dict | None:
        """Return the latest scan, None if never scanned/unknown; raise PermissionError if actor lacks access."""

def normalize(raw: dict, repo_id: str) -> dict:
    files = [{"path": f["path"], "language": f.get("language"), "kind": f.get("kind", "source"),
              "is_test": bool(f.get("is_test") or f.get("kind") == "test"),
              "imports": list(f.get("imports", [])), "symbols": list(f.get("symbols", []))}
             for f in raw.get("files", []) if f.get("path")]
    return {"repo_id": raw.get("repo_id", repo_id), "scan_id": str(raw.get("scan_id") or raw.get("scanned_at") or ""),
            "scanned_at": raw.get("scanned_at"), "files": files, "apis": raw.get("apis", []),
            "configs": raw.get("configs", []), "dependencies": raw.get("dependencies", [])}

class FileScanClient(Module01Client):
    """Reads Module 01 scan exports: <FORGEX_M01_SCAN_DIR>/<repo_id>.json"""
    def __init__(self, directory: str):
        self.dir = Path(directory)

    def _load(self, repo_id):
        p = self.dir / f"{repo_id}.json"
        if self.dir not in p.resolve().parents or not p.exists():
            return None
        return json.loads(p.read_text())

    def list_repositories(self, actor):
        out = []
        for p in sorted(self.dir.glob("*.json")) if self.dir.exists() else []:
            raw = json.loads(p.read_text())
            if not raw.get("allowed_users") or actor in raw["allowed_users"]:
                out.append({"repo_id": raw.get("repo_id", p.stem), "scanned_at": raw.get("scanned_at")})
        return out

    def get_scan(self, repo_id, actor):
        raw = self._load(repo_id)
        if raw is None:
            return None
        if raw.get("allowed_users") and actor not in raw["allowed_users"]:
            raise PermissionError(f"{actor} has no access to repository {repo_id}")
        return normalize(raw, repo_id)

class HttpModule01Client(Module01Client):
    """Calls the running Module 01 API. Paths configurable via env."""
    def __init__(self):
        self.base = os.environ["FORGEX_M01_URL"].rstrip("/")
        self.repos_path = os.getenv("FORGEX_M01_REPOS_PATH", "/repositories")
        self.scan_path = os.getenv("FORGEX_M01_SCAN_PATH", "/repositories/{repo_id}/scan")

    def _get(self, path, actor):
        import httpx
        r = httpx.get(self.base + path, headers={"X-Actor": actor}, timeout=20)
        if r.status_code == 403: raise PermissionError("forbidden by Module 01")
        if r.status_code == 404: return None
        r.raise_for_status()
        return r.json()

    def list_repositories(self, actor):
        data = self._get(self.repos_path, actor) or []
        items = data.get("items", data) if isinstance(data, dict) else data
        return [{"repo_id": str(i.get("repo_id") or i.get("id")), "scanned_at": i.get("scanned_at")} for i in items]

    def get_scan(self, repo_id, actor):
        raw = self._get(self.scan_path.format(repo_id=repo_id), actor)
        return normalize(raw, repo_id) if raw else None

_client = None
def get_m01() -> Module01Client:
    global _client
    if _client is None:
        _client = HttpModule01Client() if os.getenv("FORGEX_M01_MODE") == "http" else \
            FileScanClient(os.getenv("FORGEX_M01_SCAN_DIR", "./module01_data"))
    return _client
