"""Repository ingestion: approved-root validation and hardened ZIP extraction."""
from __future__ import annotations

import re
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

from . import config


class IngestError(ValueError):
    pass


def resolve_local(path: str) -> Path:
    try:
        r = Path(path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise IngestError("Path does not exist or cannot be resolved.")
    if not r.is_dir():
        raise IngestError("Path is not a directory.")
    if not any(r == root or root in r.parents for root in config.SCAN_ROOTS):
        raise IngestError("Path is outside the approved scan roots (FORGEX_SCAN_ROOTS).")
    return r


def extract_zip(zip_path: Path, dest: Path) -> Path:
    """Extract safely: no traversal, no symlinks, size/entry/ratio limits. Returns repo root."""
    dest.mkdir(parents=True, exist_ok=True)
    dest_r = dest.resolve()
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError):
        shutil.rmtree(dest, ignore_errors=True)
        raise IngestError("Not a valid ZIP archive.")
    try:
        with zf:
            infos = zf.infolist()
            if len(infos) > config.MAX_ZIP_ENTRIES:
                raise IngestError("Archive has too many entries.")
            if sum(i.file_size for i in infos) > config.MAX_ZIP_UNCOMPRESSED:
                raise IngestError("Archive uncompressed size exceeds the limit (possible archive bomb).")
            total = 0
            for info in infos:
                name = info.filename.replace("\\", "/")
                pp = PurePosixPath(name)
                if name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in pp.parts:
                    raise IngestError(f"Unsafe path in archive: {name[:80]!r}")
                if stat.S_ISLNK(info.external_attr >> 16):
                    continue  # never materialise symlinks
                if any(p in config.IGNORE_DIRS for p in pp.parts):
                    continue
                target = (dest_r / name).resolve()
                if target != dest_r and dest_r not in target.parents:
                    raise IngestError("Archive entry escapes the extraction directory.")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if (info.compress_size and info.file_size > (1 << 20)
                        and info.file_size / info.compress_size > config.MAX_ZIP_RATIO):
                    raise IngestError("Suspicious compression ratio (possible archive bomb).")
                target.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with zf.open(info) as src, open(target, "wb") as out:
                    while chunk := src.read(65536):
                        written += len(chunk)
                        total += len(chunk)
                        if written > config.MAX_ZIP_FILE_BYTES or total > config.MAX_ZIP_UNCOMPRESSED:
                            raise IngestError("Archive content exceeds extraction limits.")
                        out.write(chunk)
    except IngestError:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    except (zipfile.BadZipFile, OSError, RuntimeError) as e:
        shutil.rmtree(dest, ignore_errors=True)
        raise IngestError(f"Archive could not be extracted: {type(e).__name__}")
    entries = [p for p in dest.iterdir() if p.name != "__MACOSX"]
    return entries[0] if len(entries) == 1 and entries[0].is_dir() else dest
