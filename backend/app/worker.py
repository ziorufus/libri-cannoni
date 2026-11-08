"""Processo in background che fa avanzare i libri nelle fasi.

Gira dentro FastAPI (un task asyncio per ogni worker uvicorn). Con più worker
non ci sono doppie esecuzioni grazie a un lock per libro ("lease") preso con un
UPDATE condizionale, che SQLite esegue in modo atomico: solo un worker riesce a
prendere un dato libro. Se un worker muore a metà lavoro, il lease scade e un
altro worker riprende il libro.
"""

import asyncio
import logging
import os
import socket
import uuid
from datetime import timedelta

from sqlalchemy import and_, or_, select, update

from .db import SessionLocal, utcnow
from .models import Book, PhaseRun
from .phases import ACTIVE_PHASES, PHASES, BookSnapshot, PhaseResult, initial_status
from .settings_store import DEFINITIONS, get_setting

log = logging.getLogger(__name__)
LEASE_MARGIN_SECONDS = 120


def _eligible(now):
    lease_free = or_(Book.lease_until.is_(None), Book.lease_until < now)
    due = or_(
        Book.status == "ready",
        and_(Book.status == "waiting", Book.next_attempt_at <= now),
        Book.status == "running",  # "running" con lease scaduto: worker morto
    )
    return and_(Book.phase.in_(ACTIVE_PHASES), lease_free, due)


class Worker:
    def __init__(self) -> None:
        self.id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    # --- ciclo di vita -------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="books-worker")
        log.info("Worker %s avviato", self.id)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def wake(self) -> None:
        """Sveglia subito il worker di questo processo (es. dopo un inserimento)."""
        self._wake.set()

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Errore nel ciclo del worker")
            interval = await asyncio.to_thread(self._interval)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=interval)
            except TimeoutError:
                pass
            self._wake.clear()

    def _interval(self) -> int:
        with SessionLocal() as db:
            return get_setting(db, "worker_interval_seconds")

    # --- lavoro --------------------------------------------------------------

    async def tick(self) -> None:
        while (snapshot := await asyncio.to_thread(self._claim_next)) is not None:
            started = utcnow()
            try:
                result = await PHASES[snapshot.phase].handler(snapshot)
            except asyncio.CancelledError:
                await asyncio.to_thread(self._release, snapshot.id)
                raise
            except Exception as exc:
                log.exception("Errore nella fase %s del libro %s", snapshot.phase, snapshot.id)
                result = PhaseResult(False, error=f"Errore interno: {exc!r}")
            await asyncio.to_thread(self._finish, snapshot, result, started)

    def _claim_next(self) -> BookSnapshot | None:
        with SessionLocal() as db:
            settings = {d.key: get_setting(db, d.key) for d in DEFINITIONS}
            lease = settings["claude_timeout_seconds"] + LEASE_MARGIN_SECONDS
            now = utcnow()
            candidates = db.scalars(
                select(Book.id).where(_eligible(now)).order_by(Book.id).limit(20)
            ).all()
            for book_id in candidates:
                claimed = db.execute(
                    update(Book)
                    .where(Book.id == book_id, _eligible(now))
                    .values(
                        status="running",
                        lease_owner=self.id,
                        lease_until=now + timedelta(seconds=lease),
                    )
                    .execution_options(synchronize_session=False)
                )
                db.commit()
                if claimed.rowcount != 1:
                    continue  # preso da un altro worker
                book = db.get(Book, book_id)
                db.refresh(book)
                return BookSnapshot(
                    id=book.id,
                    phase=book.phase,
                    attempt=book.attempts + 1,
                    notes=book.notes,
                    photo_files=[p.filename for p in book.photos],
                    settings=settings,
                )
        return None

    def _owned(self, db, book_id: int) -> Book | None:
        book = db.get(Book, book_id)
        if book is None or book.lease_owner != self.id:
            return None  # eliminato o resettato da un admin nel frattempo
        return book

    def _release(self, book_id: int) -> None:
        with SessionLocal() as db:
            if book := self._owned(db, book_id):
                book.lease_owner = None
                book.lease_until = None
                book.status = "ready"
                db.commit()

    def _finish(self, snap: BookSnapshot, result: PhaseResult, started) -> None:
        now = utcnow()
        with SessionLocal() as db:
            book = self._owned(db, snap.id)
            if book is None or book.phase != snap.phase:
                log.info("Libro %s modificato durante la fase %s: risultato scartato", snap.id, snap.phase)
                return
            db.add(
                PhaseRun(
                    book_id=book.id,
                    phase=snap.phase,
                    attempt=snap.attempt,
                    worker=self.id,
                    started_at=started,
                    finished_at=now,
                    success=result.ok,
                    output=result.output,
                    error=result.error,
                )
            )
            book.lease_owner = None
            book.lease_until = None
            if result.ok:
                for key, value in result.updates.items():
                    setattr(book, key, value)
                book.phase = snap.phase + 1
                book.status = initial_status(book.phase)
                book.attempts = 0
                book.next_attempt_at = None
                book.last_error = ""
                log.info("Libro %s: fase %s completata", book.id, snap.phase)
            else:
                book.attempts = snap.attempt
                book.last_error = result.error
                max_attempts = snap.settings["phase_max_attempts"]
                if book.attempts >= max_attempts:
                    book.status = "failed"
                    book.next_attempt_at = None
                    log.warning("Libro %s: fase %s fallita definitivamente", book.id, snap.phase)
                else:
                    delay = snap.settings["phase_retry_delay_minutes"]
                    book.status = "waiting"
                    book.next_attempt_at = now + timedelta(minutes=delay)
                    log.warning(
                        "Libro %s: fase %s fallita (tentativo %s/%s), riprovo tra %s min",
                        book.id, snap.phase, book.attempts, max_attempts, delay,
                    )
            db.commit()


worker = Worker()
