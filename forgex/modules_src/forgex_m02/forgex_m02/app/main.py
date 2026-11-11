from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from . import models  # noqa: F401  (register tables)
from .db import Base, engine
from .errors import DomainError
from .routes import router

Base.metadata.create_all(engine)
app = FastAPI(title="FORGEX Module 02 - Autonomous Engineering Planner")
STATIC = Path(__file__).resolve().parent.parent / "static"

@app.exception_handler(DomainError)
async def domain_error(_: Request, e: DomainError):
    return JSONResponse(status_code=e.status, content={"error": {"code": e.code, "message": e.message}})

app.include_router(router)  # in the main FORGEX app: app.include_router(app.routes.router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")
