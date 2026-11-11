"""
AURA Financial Platform - Core Configuration
Unified configuration supporting all 10 modules + PayPal Sandbox/Live integration + Gemini Generative AI.
"""
from typing import List, Optional
import os
from pydantic_settings import BaseSettings
from pydantic import Field

# Ensure data directory exists
_base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_data_dir = os.path.join(_base_dir, "data")
os.makedirs(_data_dir, exist_ok=True)
_default_sqlite_path = os.path.join(_data_dir, "aura.db").replace("\\", "/")


class Settings(BaseSettings):
    APP_NAME: str = "AURA Financial Intelligence & Resolution Platform"
    APP_VERSION: str = "2.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    
    # API & CORS
    API_V1_PREFIX: str = "/api/v1"
    CORS_ORIGINS: List[str] = ["*"]
    
    # Database
    DATABASE_URL: str = Field(default=f"sqlite:///{_default_sqlite_path}")
    
    # Security & RBAC
    SECRET_KEY: str = Field(default="aura_super_secure_secret_key_financial_ai_2026")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    API_KEY_HEADER: str = "X-AURA-API-KEY"
    DEFAULT_API_KEY: str = "aura_live_api_key_sandbox_2026"
    
    # PayPal Configuration
    PAYPAL_MODE: str = Field(default="sandbox")
    PAYPAL_CLIENT_ID: Optional[str] = Field(default=None)
    PAYPAL_CLIENT_SECRET: Optional[str] = Field(default=None)
    PAYPAL_WEBHOOK_ID: Optional[str] = Field(default=None)
    PAYPAL_BASE_URL: str = Field(default="https://api-m.sandbox.paypal.com")
    
    # Gemini AI Engine
    GEMINI_API_KEY: Optional[str] = Field(default=None)
    COPILOT_MODEL: str = "gemini-3.8-flash"
    
    class Config:
        env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), ".env")
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "allow"


settings = Settings()

# Runtime config dictionary initialized from settings
_runtime_paypal_config = {
    "PAYPAL_MODE": settings.PAYPAL_MODE or os.getenv("PAYPAL_MODE", "sandbox"),
    "PAYPAL_CLIENT_ID": settings.PAYPAL_CLIENT_ID or os.getenv("PAYPAL_CLIENT_ID", ""),
    "PAYPAL_CLIENT_SECRET": settings.PAYPAL_CLIENT_SECRET or os.getenv("PAYPAL_CLIENT_SECRET", ""),
    "PAYPAL_WEBHOOK_ID": settings.PAYPAL_WEBHOOK_ID or os.getenv("PAYPAL_WEBHOOK_ID", ""),
    "PAYPAL_BASE_URL": settings.PAYPAL_BASE_URL or os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com"),
}

def get_paypal_settings() -> dict:
    return _runtime_paypal_config

def update_paypal_settings(new_cfg: dict) -> dict:
    for k, v in new_cfg.items():
        if k in _runtime_paypal_config and v is not None:
            _runtime_paypal_config[k] = v
            if k == "PAYPAL_MODE" and "PAYPAL_BASE_URL" not in new_cfg:
                _runtime_paypal_config["PAYPAL_BASE_URL"] = (
                    "https://api-m.sandbox.paypal.com" if v == "sandbox" else "https://api-m.paypal.com"
                )
    return _runtime_paypal_config
