"""
Master API v1 Router - Mounts all 10 Modules + PayPal Integration
"""
from fastapi import APIRouter
from app.api.v1.m01_routes import router as m01_router
from app.api.v1.m02_routes import router as m02_router
from app.api.v1.m03_routes import router as m03_router
from app.api.v1.m04_routes import router as m04_router
from app.api.v1.m05_routes import router as m05_router
from app.api.v1.m06_routes import router as m06_router
from app.api.v1.m07_routes import router as m07_router
from app.api.v1.m08_routes import router as m08_router
from app.api.v1.m09_routes import router as m09_router
from app.api.v1.m10_routes import router as m10_router
from app.api.v1.paypal_routes import router as paypal_router
from app.api.v1.workflows_routes import router as workflows_router
from app.api.v1.overview_routes import router as overview_router
from app.api.v1.workbench_routes import router as workbench_router

api_v1_router = APIRouter()

api_v1_router.include_router(workbench_router)
api_v1_router.include_router(overview_router)
api_v1_router.include_router(paypal_router)
api_v1_router.include_router(workflows_router)
api_v1_router.include_router(m01_router)
api_v1_router.include_router(m02_router)
api_v1_router.include_router(m03_router)
api_v1_router.include_router(m04_router)
api_v1_router.include_router(m05_router)
api_v1_router.include_router(m06_router)
api_v1_router.include_router(m07_router)
api_v1_router.include_router(m08_router)
api_v1_router.include_router(m09_router)
api_v1_router.include_router(m10_router)
