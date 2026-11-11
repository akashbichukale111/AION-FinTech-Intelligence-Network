"""
AURA Financial Platform - Server Launcher
Starts the unified FastAPI application on http://127.0.0.1:8000
"""
import sys
import os
import uvicorn

# Add backend directory to sys.path
backend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

if __name__ == "__main__":
    print("=" * 70)
    print("  AURA Financial Intelligence & Resolution Platform (v2.0.0)")
    print("  Unified 10-Module Platform + PayPal Sandbox Integration")
    print("=" * 70)
    print("  Backend API:  http://127.0.0.1:8000/api/v1")
    print("  API Docs:     http://127.0.0.1:8000/docs")
    print("  UI Dashboard: http://127.0.0.1:8000/")
    print("=" * 70)
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
