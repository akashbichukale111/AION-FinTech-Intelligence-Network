"""Standalone launcher:  uvicorn app_demo:app --port 8005   ->  http://localhost:8005/api/v1/testing/ui"""
from fastapi import FastAPI
from forgex.testing import router
app = FastAPI(title="FORGEX Module 05")
app.include_router(router)
