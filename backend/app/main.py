import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .access import ensure_admin
from .config import config
from .db import Base, SessionLocal, engine, init_lock
from .routers import access, admin_settings, auth, books, jobs, vinted
from .security import CurrentUser, require_user
from .settings_store import seed_defaults
from .worker import worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def init_db() -> None:
    with init_lock():
        Base.metadata.create_all(engine)
        with SessionLocal() as db:
            ensure_admin(db, config.admin_email)
            seed_defaults(db)
    config.books_dir.mkdir(parents=True, exist_ok=True)
    config.thumbs_dir.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    app.state.worker = worker
    worker.start()
    yield
    await worker.stop()


app = FastAPI(title="Mettete dei libri nei vostri cannoni", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.frontend_origin],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(books.router)
app.include_router(books.photos_router)
app.include_router(access.router)
app.include_router(admin_settings.router)
app.include_router(vinted.router)
app.include_router(jobs.router)


@app.get("/api/health")
def health():
    return {"ok": True}


# Parte semi-pubblica (per ora vuota): utenti abilitati e amministratori
@app.get("/api/area/ping")
def area_ping(cu: CurrentUser = Depends(require_user)):
    return {"ok": True, "role": cu.role}
