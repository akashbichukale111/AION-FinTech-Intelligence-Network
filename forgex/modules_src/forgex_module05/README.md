# FORGEX Module 05 — Testing & Regression Intelligence Engine

Mount: `from forgex.testing import router, providers` → `app.include_router(router)`
Standalone: `pip install fastapi uvicorn pydantic pytest coverage` → `uvicorn app_demo:app --port 8005` → open `/api/v1/testing/ui`
Wire Modules 01–04: `providers.configure(repository=..., plan=..., workspace=..., security=...)` (protocols in `integrations.py`).
Checks: `python -m pytest tests -q`
Env: `FORGEX_TESTING_DB`, `FORGEX_TESTING_TOKEN` (optional run token), `FORGEX_TEST_IMAGE` (docker image), `FORGEX_KEEP_WORKSPACE`.
