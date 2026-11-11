# FORGEX Module 09 - Production Incident & Observability Intelligence Engine
Run:   `M09_API_KEYS="admin1:admin,ing1:ingest" uvicorn app.main:app --port 8009`  then open http://localhost:8009 (paste the key in the header box)
Demo:  click **Load synthetic demo** then **Run detection** (synthetic data is always labelled; purge with `DELETE /api/v1/observability/synthetic`)
Tests: `python -m pytest tests -q`
Keys/roles: viewer(read) · ingest(ingest only) · analyst(read+write) · admin(all). Unset M09_API_KEYS -> random admin key printed at start.
Module bridges (all optional, read-only/dry-run): FORGEX_M01_ARCH_FILE, FORGEX_M0{1,2,3,5,6,7,8}_URL, FORGEX_INTERNAL_KEY. Edit `ROUTES` in app/integrations.py to match real FORGEX routes.
Tuning: M09_THRESHOLDS (JSON), M09_SLO_FILE (JSON {"default":{...},"payment-service":{...}}), M09_RETENTION_ROWS, M09_MAX_BATCH.
