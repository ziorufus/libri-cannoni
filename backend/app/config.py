"""Configurazione da variabili d'ambiente (o file .env nella cartella backend)."""

from functools import cached_property
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Obbligatorie
    admin_email: str
    books_dir: Path
    secret_key: str

    # Database (SQLite)
    database_url: str = "sqlite:///./data/libri.db"

    # Indirizzi pubblici di frontend e backend (servono per redirect OAuth e CORS)
    frontend_url: str = "http://localhost:5173"
    backend_url: str = "http://localhost:8000"

    # Claude Code
    claude_code_oauth_token: str = ""
    claude_bin: str = "claude"

    # Login con servizi terzi
    google_client_id: str = ""
    google_client_secret: str = ""
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_tenant: str = "common"

    # Durata del token di sessione
    jwt_expire_hours: int = 24 * 7

    @cached_property
    def thumbs_dir(self) -> Path:
        # Le miniature stanno fuori dalle cartelle dei libri, così ogni
        # cartella <BOOKS_DIR>/<id> contiene solo le fotografie originali.
        return self.books_dir / ".thumbs"

    @property
    def frontend_origin(self) -> str:
        return self.frontend_url.rstrip("/")


config = Config()
