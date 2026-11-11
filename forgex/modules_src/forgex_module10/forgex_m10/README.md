# FORGEX Module 10 — Release Risk, Governance & Assurance
Run: `pip install -r requirements.txt && uvicorn main:app --port 8010` → UI at `/center`, API docs at `/docs`, tests: `pytest -q`.
First start prints a one-time admin key (or set `FORGEX_BOOTSTRAP_ADMIN_KEY`). Create users via `POST /api/v1/governance/users`.
Env: `FORGEX_M10_DB`, `FORGEX_MODULE_URLS` (JSON `{"tests":"http://m05/…?commit={commit}"}` adapters for Modules 01–09), `FORGEX_GITHUB_TOKEN`.
Evidence kinds: architecture(M01) plan(M02) patch(M03) nightshift(M04) tests(M05) fintech(M06) api(M07) security(M08) incidents(M09) config/migration/rollback.
