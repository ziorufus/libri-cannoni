import fcntl
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import DateTime, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.types import TypeDecorator

from .config import config


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """SQLite non conserva il fuso orario: salviamo in UTC e lo rimettiamo in lettura."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class Base(DeclarativeBase):
    pass


_url = make_url(config.database_url)
_db_file: Path | None = None
if _url.get_backend_name() == "sqlite" and _url.database not in (None, "", ":memory:"):
    _db_file = Path(_url.database).resolve()
    _db_file.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    config.database_url,
    connect_args={"check_same_thread": False, "timeout": 30},
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    # WAL + busy_timeout: più worker uvicorn possono usare lo stesso file.
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_db():
    with SessionLocal() as db:
        yield db


def lock_path(name: str) -> Path:
    """File di lock accanto al DB (deve stare su un disco locale, non di rete)."""
    return (_db_file.parent if _db_file else Path(".")) / name


@contextmanager
def init_lock():
    """Lock su file: evita che più worker creino/popolino il DB contemporaneamente."""
    lock_path_ = lock_path(".init.lock")
    with open(lock_path_, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
