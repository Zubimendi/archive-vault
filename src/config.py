import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "ArchiveVault"
    VERSION: str = "1.0.0"
    DEBUG: bool = False
    
    # Primary ArchiveVault Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", 
        "postgresql+asyncpg://archivevault:archivevault_secret@localhost:5434/archivevault_db"
    )
    
    # Secondary Downstream App Database for Connectors & Tests
    DOWNSTREAM_DB_URL: str = os.getenv(
        "DOWNSTREAM_DB_URL",
        "postgresql+asyncpg://appuser:appsecret@localhost:5435/app_downstream_db"
    )
    
    # Cryptographic Pepper for Suppression Ledger HMAC
    SUPPRESSION_PEPPER: str = os.getenv(
        "SUPPRESSION_PEPPER",
        "d7a95b87c12f43a985e13d96420f18c2a938b8d4f201e7a346b98e1f57c2a490"
    )
    
    # Master Key for KMS / KEK simulation (32 bytes AES-256)
    MASTER_KEK_SECRET: str = os.getenv(
        "MASTER_KEK_SECRET",
        "4f2a7e91b6c8d3502819f7a4e6c3b28190d7e6f5c4b3a291807f6e5d4c3b2a10"
    )
    
    # Default Regime Deadlines in Days
    GDPR_DEADLINE_DAYS: int = 30
    CCPA_DEADLINE_DAYS: int = 45
    DEFAULT_QUARANTINE_DAYS: int = 3

settings = Settings()
