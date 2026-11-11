"""Runtime configuration (env-driven). All limits are configurable."""
from __future__ import annotations

import os
from pathlib import Path


def _paths(v: str) -> list[Path]:
    return [Path(p).expanduser().resolve() for p in v.split(os.pathsep) if p.strip()]


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


DATA_DIR = Path(os.environ.get("FORGEX_DATA_DIR", "./forgex_data")).expanduser().resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
EXTRACT_DIR = DATA_DIR / "extracted"
DB_URL = os.environ.get("FORGEX_DATABASE_URL", f"sqlite:///{DATA_DIR / 'forgex.db'}")

# Local-path scans are only allowed inside these roots (os.pathsep separated).
SCAN_ROOTS = _paths(os.environ.get("FORGEX_SCAN_ROOTS", "")) or [Path.home().resolve()]

DEFAULT_IGNORE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", ".env.d", "__pycache__",
    "dist", "build", "target", "out", ".next", ".nuxt", ".cache", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tox", "site-packages", ".idea", ".vscode",
    "coverage", ".gradle", "vendor", "bower_components", ".terraform",
}
IGNORE_DIRS = DEFAULT_IGNORE_DIRS | {
    x.strip() for x in os.environ.get("FORGEX_IGNORE_DIRS", "").split(",") if x.strip()
}

MAX_FILES = _int("FORGEX_MAX_FILES", 50_000)
MAX_READ_BYTES = _int("FORGEX_MAX_READ_BYTES", 1_000_000)          # per-file read cap
MAX_UPLOAD_BYTES = _int("FORGEX_MAX_UPLOAD_BYTES", 200 * 1024 * 1024)
MAX_ZIP_ENTRIES = _int("FORGEX_MAX_ZIP_ENTRIES", 50_000)
MAX_ZIP_UNCOMPRESSED = _int("FORGEX_MAX_ZIP_UNCOMPRESSED", 500 * 1024 * 1024)
MAX_ZIP_FILE_BYTES = _int("FORGEX_MAX_ZIP_FILE_BYTES", 100 * 1024 * 1024)
MAX_ZIP_RATIO = _int("FORGEX_MAX_ZIP_RATIO", 100)

LARGE_FILE_LINES = _int("FORGEX_LARGE_FILE_LINES", 1000)
HIGH_FAN_IN = _int("FORGEX_HIGH_FAN_IN", 8)
HIGH_FAN_OUT = _int("FORGEX_HIGH_FAN_OUT", 15)
DUP_MIN_BYTES = _int("FORGEX_DUP_MIN_BYTES", 256)
