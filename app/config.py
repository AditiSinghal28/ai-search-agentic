import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "medical_ai_search_agentic")
    api_host: str = os.getenv("API_HOST", "127.0.0.1")
    api_port: int = int(os.getenv("API_PORT", "8001"))

    db_host: str = os.getenv("DB_HOST", "127.0.0.1")
    db_port: int = int(os.getenv("DB_PORT", "3306"))
    db_name: str = os.getenv("DB_NAME", "mbs")
    db_user: str = os.getenv("DB_USER", "root")
    db_password: str = os.getenv("DB_PASSWORD", "")

    ollama_enabled: bool = os.getenv("OLLAMA_ENABLED", "true").lower() == "true"
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")
    ollama_timeout_seconds: int = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "90"))

    default_currency: str = os.getenv("DEFAULT_CURRENCY", "RM")
    max_sql_rows: int = int(os.getenv("MAX_SQL_ROWS", "200"))
    sql_retry_count: int = int(os.getenv("SQL_RETRY_COUNT", "2"))
    active_booking_statuses: tuple[str, ...] = tuple(
        s.strip()
        for s in os.getenv(
            "ACTIVE_BOOKING_STATUSES",
            "pending,accepted,rescheduled,completed",
        ).split(",")
        if s.strip()
    )


settings = Settings()
