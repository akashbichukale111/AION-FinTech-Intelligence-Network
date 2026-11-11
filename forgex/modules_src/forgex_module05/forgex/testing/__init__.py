"""FORGEX Module 05 - Testing & Regression Intelligence Engine.
Mount with:  from forgex.testing import router;  app.include_router(router)"""
from .router import router
from .integrations import providers
from . import interface
__all__ = ["router", "providers", "interface"]
