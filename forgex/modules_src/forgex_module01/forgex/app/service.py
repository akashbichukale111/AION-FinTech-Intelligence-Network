from __future__ import annotations

import logging
import shutil

from . import config, ingest, pipeline
from .db import Repository, SessionLocal, utcnow

log = logging.getLogger("forgex")


def execute_scan(repo_id: str) -> None:
    """Background job: ingest -> analyse -> persist. Never raises; failures are stored honestly."""
    with SessionLocal() as db:
        repo = db.get(Repository, repo_id)
        if repo is None:
            return
        repo.status = "running"
        db.commit()
        cleanup = []
        try:
            if repo.source_type == "zip":
                zp = config.UPLOAD_DIR / f"{repo_id}.zip"
                dest = config.EXTRACT_DIR / repo_id
                cleanup += [zp, dest]
                root = ingest.extract_zip(zp, dest)
            else:
                root = ingest.resolve_local(repo.source_ref)
            repo.result = pipeline.run(root, repo.name)
            repo.status = "done"
        except (ingest.IngestError, pipeline.AnalysisError) as e:
            repo.status, repo.error = "failed", str(e)
        except Exception as e:  # noqa: BLE001
            log.exception("scan failed")
            repo.status, repo.error = "failed", f"Unexpected error: {type(e).__name__}"
        finally:
            for p in cleanup:
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
            repo.finished_at = utcnow()
            db.commit()
