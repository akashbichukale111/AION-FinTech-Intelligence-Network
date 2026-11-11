# FORGEX Module 01: Repository & Architecture Intelligence Engine

    pip install -r requirements.txt
    export FORGEX_SCAN_ROOTS=/path/to/your/projects   # approved scan locations (default: home dir)
    uvicorn app.main:app --reload                     # UI at http://127.0.0.1:8000
    pip install pytest httpx && pytest -q tests

Env: FORGEX_DATABASE_URL (PostgreSQL URL, default SQLite), FORGEX_DATA_DIR, FORGEX_IGNORE_DIRS (comma list),
FORGEX_MAX_FILES, FORGEX_MAX_UPLOAD_BYTES, FORGEX_LARGE_FILE_LINES, FORGEX_MAX_ZIP_* limits.
API docs: /docs
