import io, json, os, tempfile, zipfile
from pathlib import Path

_T = Path(tempfile.mkdtemp())
os.environ["FORGEX_DATA_DIR"] = str(_T / "data")
os.environ["FORGEX_SCAN_ROOTS"] = str(_T / "repos")
(_T / "repos").mkdir()

import pytest
from fastapi.testclient import TestClient

from app import ingest
from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def make_repo() -> Path:
    r = _T / "repos" / "demo"
    (r / "backend/app").mkdir(parents=True)
    (r / "frontend/src").mkdir(parents=True)
    (r / "tests").mkdir()
    (r / "node_modules/x").mkdir(parents=True)
    (r / "node_modules/x/i.js").write_text("ignored")
    (r / "backend/requirements.txt").write_text("fastapi==0.110.0\nsqlalchemy\npsycopg2-binary>=2.9\n")
    (r / "backend/app/__init__.py").write_text("")
    (r / "backend/app/main.py").write_text(
        "from fastapi import FastAPI\nfrom . import models\napp = FastAPI()\n\n@app.get('/items')\ndef items(): return []\n")
    (r / "backend/app/models.py").write_text(
        "from sqlalchemy.orm import DeclarativeBase\nfrom . import main\nclass Base(DeclarativeBase): pass\nclass Item(Base):\n    __tablename__='items'\n")
    (r / "frontend/package.json").write_text(json.dumps({"dependencies": {"react": "^18.2.0"}, "scripts": {"start": "vite"}}))
    (r / "frontend/src/App.jsx").write_text("import {x} from './util'\nfetch('/api/items')\n")
    (r / "frontend/src/util.js").write_text("export const x=1\n")
    (r / "tests/test_main.py").write_text("from backend.app import main\n")
    (r / "docker-compose.yml").write_text("services:\n  db:\n    image: postgres:16\n  api:\n    build: ./backend\n    depends_on:\n      - db\n")
    (r / ".env").write_text("DB_PASSWORD=SuperSecret123\nURL=postgresql://u:pw@h/db\n")
    (r / "bad").mkdir()
    (r / "bad/package.json").write_text("{not json")
    (r / "dup1.py").write_text("x = 1\n" * 100)
    (r / "dup2.py").write_text("x = 1\n" * 100)
    return r


def wait_done(client, rid):
    d = client.get(f"/api/v1/repositories/{rid}").json()
    assert d["status"] in ("done", "failed"), d
    return d


def test_local_scan_end_to_end(client):
    r = make_repo()
    resp = client.post("/api/v1/repositories/scan", data={"path": str(r)})
    assert resp.status_code == 202
    rid = resp.json()["id"]
    d = wait_done(client, rid)
    assert d["status"] == "done", d.get("error")
    names = {f["name"] for f in d["stack"]["frameworks"]}
    assert {"FastAPI", "React", "SQLAlchemy"} <= names
    assert any(x["name"] == "PostgreSQL" for x in d["stack"]["databases"])
    assert "Docker Compose" in {i["name"] for i in d["stack"]["infrastructure"]}
    dep = client.get(f"/api/v1/repositories/{rid}/dependencies").json()
    assert any(x["name"] == "fastapi" and x["version"] == "0.110.0" and x["line"] == 1 for x in dep["dependencies"])
    assert any(m["error"] and "package.json" in m["path"] for m in dep["manifests"])  # malformed handled
    arch = client.get(f"/api/v1/repositories/{rid}/architecture").json()
    assert any(rt["path"] == "/items" and rt["method"] == "GET" for rt in arch["routes"])
    assert any(m["name"] == "Item" for m in arch["models"])
    assert any(e["from"].endswith("App.jsx") and e["to"].endswith("util.js") for e in arch["import_edges"])
    assert any(s["name"] == "api" and s["component"] == "backend" for s in arch["services"])
    h = client.get(f"/api/v1/repositories/{rid}/health").json()
    assert h["indicators"]["circular_dependency_groups"] == 1
    assert h["indicators"]["duplicate_content_groups"] >= 1
    rep = client.get(f"/api/v1/repositories/{rid}/report")
    raw = rep.text
    assert "SuperSecret123" not in raw and ":pw@" not in raw  # secrets redacted
    assert "node_modules" not in json.dumps(rep.json()["tree"])
    assert any(f["category"] == "security" for f in rep.json()["findings"])
    for f in rep.json()["findings"]:
        assert {"id", "title", "path", "method", "confidence", "nature", "evidence"} <= f.keys()
    md = client.get(f"/api/v1/repositories/{rid}/report", params={"format": "markdown"})
    assert md.status_code == 200 and "## 11. Analysis limitations" in md.text


def test_path_outside_roots_rejected(client):
    assert client.post("/api/v1/repositories/scan", data={"path": "/etc"}).status_code == 400
    assert client.post("/api/v1/repositories/scan", data={"path": str(_T / "nope")}).status_code == 400


def zip_bytes(entries):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        for n, c in entries:
            if isinstance(n, zipfile.ZipInfo):
                z.writestr(n, c)
            else:
                z.writestr(n, c)
    return b.getvalue()


def test_zip_scan_ok(client):
    data = zip_bytes([("proj/requirements.txt", "flask==3.0.0\n"), ("proj/app.py", "from flask import Flask\napp=Flask(__name__)\n")])
    resp = client.post("/api/v1/repositories/scan", files={"archive": ("proj.zip", data, "application/zip")})
    assert resp.status_code == 202
    d = wait_done(client, resp.json()["id"])
    assert d["status"] == "done" and any(f["name"] == "Flask" for f in d["stack"]["frameworks"])


def test_zip_traversal_and_symlink_and_bomb(tmp_path):
    p = tmp_path / "t.zip"
    p.write_bytes(zip_bytes([("../evil.txt", "x")]))
    with pytest.raises(ingest.IngestError):
        ingest.extract_zip(p, tmp_path / "o1")
    assert not (tmp_path / "evil.txt").exists()
    p.write_bytes(zip_bytes([("/abs.txt", "x")]))
    with pytest.raises(ingest.IngestError):
        ingest.extract_zip(p, tmp_path / "o2")
    zi = zipfile.ZipInfo("proj/link"); zi.external_attr = (0o120777 << 16)
    p.write_bytes(zip_bytes([(zi, "/etc/passwd"), ("proj/a.py", "x=1\n")]))
    root = ingest.extract_zip(p, tmp_path / "o3")
    assert not (root / "link").exists() and (root / "a.py").exists()
    p.write_bytes(zip_bytes([("big.txt", "0" * (5 * 1024 * 1024))]))  # highly compressible
    with pytest.raises(ingest.IngestError):
        ingest.extract_zip(p, tmp_path / "o4")


def test_invalid_zip_and_empty_dir(client):
    r = client.post("/api/v1/repositories/scan", files={"archive": ("x.zip", b"notzip", "application/zip")})
    assert r.status_code == 400
    e = _T / "repos" / "empty"; e.mkdir()
    rid = client.post("/api/v1/repositories/scan", data={"path": str(e)}).json()["id"]
    d = wait_done(client, rid)
    assert d["status"] == "failed" and d["error"]
    assert client.get(f"/api/v1/repositories/{rid}/architecture").status_code == 409


def test_symlink_not_followed(client):
    r = _T / "repos" / "sl"; r.mkdir()
    (r / "a.py").write_text("x=1\n")
    try:
        os.symlink("/etc", r / "etc_link")
    except OSError:
        pytest.skip("symlinks unsupported")
    rid = client.post("/api/v1/repositories/scan", data={"path": str(r)}).json()["id"]
    d = wait_done(client, rid)
    assert d["status"] == "done" and d["overview"]["total_files"] == 1
