from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import router
from .db import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="FORGEX Module 01: Repository & Architecture Intelligence", version="1.0.0", lifespan=lifespan)
app.include_router(router, prefix="/api/v1")
app.mount("/", StaticFiles(directory=Path(__file__).resolve().parent.parent / "static", html=True), name="ui")
