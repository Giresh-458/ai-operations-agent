import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    LANGCHAIN_TRACING_V2: str = os.getenv("LANGCHAIN_TRACING_V2", "false")
    LANGCHAIN_API_KEY: str = os.getenv("LANGCHAIN_API_KEY", "")
    LANGCHAIN_PROJECT: str = os.getenv("LANGCHAIN_PROJECT", "AIOpsAgent")
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/operations_agent"
    )
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "2"))
    # Deterministic mock execution is for local tests only; keep it off by default.
    ALLOW_OFFLINE_MODE: bool = os.getenv("ALLOW_OFFLINE_MODE", "false").lower() in {"1", "true", "yes"}
    API_ACCESS_TOKEN: str = os.getenv("API_ACCESS_TOKEN", "")
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "http://localhost:8501,http://127.0.0.1:8501")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
