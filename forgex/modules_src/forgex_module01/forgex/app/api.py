from __future__ import annotations

import uuid
import zipfile
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, ingest, report, service
from .db import Repository, SessionLocal

router = APIRouter()


def get_db():
    with SessionLocal() as db:
        yield db


class RepoOut(BaseModel):
    id: str
    name: str
    source_type: str
    source_ref: str
    status: str
    error: Optional[str] = None
    created_at: str
    finished_at: Optional[str] = None
    summary: Optional[dict[str, Any]] = None


def _out(r: Repository) -> RepoOut:
    summary = None
    if r.result:
        o = r.result["overview"]
        summary = {k: o[k] for k in ("total_files", "total_lines", "primary_language", "monorepo", "component_count")}
        summary["findings"] = len(r.result["findings"])
    return RepoOut(id=r.id, name=r.name, source_type=r.source_type, source_ref=r.source_ref, status=r.status,
                   error=r.error, created_at=r.created_at.isoformat(),
                   finished_at=r.finished_at.isoformat() if r.finished_at else None, summary=summary)


def _done(db: Session, rid: str) -> Repository:
    r = db.get(Repository, rid)
    if r is None:
        raise HTTPException(404, "Repository not found")
    if r.status != "done":
        raise HTTPException(409, f"Scan not completed (status: {r.status})" + (f": {r.error}" if r.error else ""))
    return r


@router.get("/info")
def info():
    return {"scan_roots": [str(p) for p in config.SCAN_ROOTS], "max_upload_bytes": config.MAX_UPLOAD_BYTES,
            "ignored_directories": sorted(config.IGNORE_DIRS)}


@router.post("/repositories/scan", status_code=202, response_model=RepoOut)
async def scan(background: BackgroundTasks, path: Optional[str] = Form(None), archive: Optional[UploadFile] = File(None),
               db: Session = Depends(get_db)):
    if bool(path) == bool(archive):
        raise HTTPException(400, "Provide exactly one of: 'path' (local directory) or 'archive' (ZIP upload).")
    rid = str(uuid.uuid4())
    if path:
        try:
            root = ingest.resolve_local(path)
        except ingest.IngestError as e:
            raise HTTPException(400, str(e))
        repo = Repository(id=rid, name=root.name or str(root), source_type="local", source_ref=str(root))
    else:
        fname = Path(archive.filename or "upload.zip").name
        config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        dest = config.UPLOAD_DIR / f"{rid}.zip"
        size = 0
        with open(dest, "wb") as out:
            while chunk := await archive.read(1 << 20):
                size += len(chunk)
                if size > config.MAX_UPLOAD_BYTES:
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(413, "Upload exceeds the size limit.")
                out.write(chunk)
        if not zipfile.is_zipfile(dest):
            dest.unlink(missing_ok=True)
            raise HTTPException(400, "Uploaded file is not a valid ZIP archive.")
        repo = Repository(id=rid, name=Path(fname).stem, source_type="zip", source_ref=fname)
    db.add(repo)
    db.commit()
    background.add_task(service.execute_scan, rid)
    return _out(repo)


@router.get("/repositories", response_model=list[RepoOut])
def list_repos(db: Session = Depends(get_db)):
    return [_out(r) for r in db.scalars(select(Repository).order_by(Repository.created_at.desc()).limit(200))]


@router.get("/repositories/{rid}")
def get_repo(rid: str, db: Session = Depends(get_db)):
    r = db.get(Repository, rid)
    if r is None:
        raise HTTPException(404, "Repository not found")
    out = _out(r).model_dump()
    if r.status == "done":
        res = r.result
        out.update(overview=res["overview"], stack=res["stack"], tree=res["tree"], findings=res["findings"],
                   limitations=res["limitations"])
    return out


@router.get("/repositories/{rid}/architecture")
def architecture(rid: str, db: Session = Depends(get_db)):
    return _done(db, rid).result["architecture"]


@router.get("/repositories/{rid}/dependencies")
def dependencies(rid: str, db: Session = Depends(get_db)):
    return _done(db, rid).result["dependencies"]


@router.get("/repositories/{rid}/health")
def health(rid: str, db: Session = Depends(get_db)):
    res = _done(db, rid).result
    return {**res["health"], "findings": [f for f in res["findings"] if f["category"] in ("health", "security")]}


@router.get("/repositories/{rid}/report")
def get_report(rid: str, format: Literal["json", "markdown"] = "json", db: Session = Depends(get_db)):
    res = _done(db, rid).result
    if format == "markdown":
        return PlainTextResponse(report.to_markdown(res), media_type="text/markdown")
    return res


@router.delete("/repositories/{rid}", status_code=204)
def delete_repo(rid: str, db: Session = Depends(get_db)):
    r = db.get(Repository, rid)
    if r is None:
        raise HTTPException(404, "Repository not found")
    db.delete(r)
    db.commit()
