"""Processo in background che fa avanzare i libri nelle fasi.

Gira dentro FastAPI: ogni processo uvicorn avvia il proprio ciclo, ma lavora
solo il "leader", cioè il processo che possiede un lock esclusivo sul file
data/.worker.lock (fcntl.flock). Gli altri restano in attesa e riprovano a ogni
intervallo; se il leader muore (anche con kill -9) il sistema operativo rilascia
il lock e un altro processo si auto-nomina leader al giro successivo. Quindi,
con qualunque numero di --workers, c'è un solo lavoro in background alla volta.

Prima di eseguire una fase si controllano pausa e requisiti (vedi phases.py):
i libri di una fase in pausa restano "paused" (tranne quelli forzati), quelli
con un requisito mancante diventano "blocked" e ripartono quando un admin
modifica il libro.

Come ulteriore protezione ogni libro viene comunque "prenotato" (lease) con un
UPDATE condizionale, che SQLite esegue in modo atomico: anche due backend
avviati per sbaglio non elaborano mai lo stesso libro.
"""

import asyncio
import fcntl
import logging
import os
import socket
import time
import uuid
from datetime import timedelta

from sqlalchemy import and_, or_, select, update

from .db import SessionLocal, lock_path, utcnow
from .models import Book, PhaseRun
from .phases import (
    ACTIVE_PHASES,
    PHASES,
    BookSnapshot,
    PhaseResult,
    initial_status,
    min_gap_key,
    unmet_requirements,
)
from .settings_store import DEFINITIONS, get_setting, paused_phases

log = logging.getLogger(__name__)
LEASE_MARGIN_SECONDS = 120
# Durata minima della prenotazione di un libro (es. caricamento di molte foto su Vinted)
MIN_LEASE_SECONDS = 1800


def load_settings(db) -> dict:
    return {d.key: get_setting(db, d.key) for d in DEFINITIONS}


def _not_paused(paused: list[int]):
    return or_(Book.phase.notin_(paused), Book.force_phase == Book.phase)


def apply_pauses(db, settings: dict | None = None) -> None:
    """Allinea gli stati alle pause: "ready" -> "paused" nelle fasi in pausa (salvo
    libri forzati) e viceversa. Fa commit."""
    paused = paused_phases(settings or load_settings(db))
    db.execute(
        update(Book)
        .where(Book.status == "ready", Book.phase.in_(paused), or_(Book.force_phase.is_(None), Book.force_phase != Book.phase))
        .values(status="paused")
        .execution_options(synchronize_session=False)
    )
    db.execute(
        update(Book)
        .where(Book.status == "paused", _not_paused(paused))
        .values(status="ready")
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.expire_all()  # gli oggetti già caricati rileggono lo stato aggiornato


def _eligible(now, phases, paused):
    lease_free = or_(Book.lease_until.is_(None), Book.lease_until < now)
    due = or_(
        Book.status == "ready",
        and_(Book.status == "waiting", Book.next_attempt_at <= now),
        Book.status == "running",  # "running" con lease scaduto: worker morto
    )
    return and_(Book.phase.in_(phases), _not_paused(paused), lease_free, due)


class Worker:
    def __init__(self) -> None:
        self.id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._lock_file = None  # aperto e bloccato finché questo processo è leader
        # Attese tra esecuzioni della stessa fase: solo in memoria (un riavvio le azzera)
        self._last_end: dict[int, float] = {}  # fase -> fine dell'ultima esecuzione (monotonic)
        self._next_free_in: float | None = None  # secondi alla fine della prossima attesa

    # --- leader ----------------------------------------------------------------

    @property
    def is_leader(self) -> bool:
        return self._lock_file is not None

    def _try_become_leader(self) -> bool:
        if self.is_leader:
            return True
        fh = open(lock_path(".worker.lock"), "a+")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.close()  # c'è già un leader
            return False
        fh.seek(0)
        fh.truncate()
        fh.write(f"{self.id}\n")  # solo informativo: chi è il leader
        fh.flush()
        self._lock_file = fh
        log.info("Worker %s: sono il leader, elaboro i libri", self.id)
        return True

    def _resign(self) -> None:
        if self._lock_file is not None:
            fcntl.flock(self._lock_file, fcntl.LOCK_UN)
            self._lock_file.close()
            self._lock_file = None

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
        self._resign()

    def wake(self) -> None:
        """Sveglia subito il worker di questo processo (es. dopo un inserimento).

        Ha effetto immediato solo se questo processo è il leader; altrimenti il
        leader trova il lavoro al suo controllo successivo.
        """
        self._wake.set()

    async def _loop(self) -> None:
        if not self._try_become_leader():
            log.info("Worker %s: c'è già un leader, resto in attesa", self.id)
        while True:
            try:
                if self._try_become_leader():
                    await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Errore nel ciclo del worker")
            interval = await asyncio.to_thread(self._interval)
            if self._next_free_in is not None:
                # Una fase in attesa si libera prima del prossimo controllo: svegliati allora
                interval = max(0.5, min(interval, self._next_free_in))
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
            # L'attesa della fase parte dalla fine dell'esecuzione, riuscita o fallita
            self._last_end[snapshot.phase] = time.monotonic()
            await asyncio.to_thread(self._finish, snapshot, result, started)

    def _claim_next(self) -> BookSnapshot | None:
        with SessionLocal() as db:
            settings = load_settings(db)
            apply_pauses(db, settings)
            paused = paused_phases(settings)
            lease = max(settings["claude_timeout_seconds"], MIN_LEASE_SECONDS) + LEASE_MARGIN_SECONDS
            phases = self._phases_ready(settings)
            if not phases:
                return None
            now = utcnow()
            candidates = db.scalars(
                select(Book.id).where(_eligible(now, phases, paused)).order_by(Book.id).limit(20)
            ).all()
            for book_id in candidates:
                claimed = db.execute(
                    update(Book)
                    .where(Book.id == book_id, _eligible(now, phases, paused))
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
                if reasons := unmet_requirements(book, settings):
                    # Requisito mancante: il libro resta fermo finché non lo si corregge
                    book.status = "blocked"
                    book.last_error = "\n".join(reasons)
                    book.lease_owner = None
                    book.lease_until = None
                    db.commit()
                    log.info("Libro %s bloccato in fase %s: %s", book.id, book.phase, book.last_error)
                    continue
                return BookSnapshot(
                    id=book.id,
                    phase=book.phase,
                    attempt=book.attempts + 1,
                    notes=book.notes,
                    photo_files=[p.filename for p in book.photos],
                    settings=settings,
                    claude_output=book.claude_output,
                    description=book.description,
                    price=book.price,
                    condition=book.condition,
                    condition_manual=bool(book.condition_manual),
                    vinted_draft=book.vinted_draft,
                )
        return None

    def _phases_ready(self, settings: dict) -> list[int]:
        """Fasi automatiche che hanno finito la loro attesa minima."""
        mono = time.monotonic()
        ready, waits = [], []
        for phase in ACTIVE_PHASES:
            last = self._last_end.get(phase)
            remaining = 0 if last is None else last + settings[min_gap_key(phase)] - mono
            if remaining > 0:
                waits.append(remaining)
            else:
                ready.append(phase)
        self._next_free_in = min(waits) if waits else None
        return ready

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
                # Controllato qui, al momento del salvataggio: se nel frattempo un
                # admin ha impostato il valore a mano, quello resta.
                for key, value in result.fill_if_empty.items():
                    if getattr(book, key) in (None, ""):
                        setattr(book, key, value)
                book.phase = snap.phase + 1
                book.status = initial_status(book.phase)
                book.force_phase = None
                book.attempts = 0
                book.next_attempt_at = None
                book.last_error = ""
                if result.next_phase_delay_minutes and book.status == "ready":
                    book.status = "waiting"
                    book.next_attempt_at = now + timedelta(minutes=result.next_phase_delay_minutes)
                log.info("Libro %s: fase %s completata", book.id, snap.phase)
            elif result.goto_phase is not None:
                # Es. Vinted ha riportato l'annuncio in bozza: si torna indietro, fermi
                for key, value in result.updates.items():
                    setattr(book, key, value)
                book.phase = result.goto_phase
                book.status = "idle"
                book.force_phase = None
                book.attempts = 0
                book.next_attempt_at = None
                book.last_error = result.error
                log.warning("Libro %s: fase %s -> torna alla fase %s: %s", book.id, snap.phase, book.phase, result.error)
            else:
                book.attempts = snap.attempt
                book.last_error = result.error
                max_attempts = result.max_attempts or snap.settings["phase_max_attempts"]
                if book.attempts >= max_attempts or not result.retry:
                    book.status = "failed"
                    book.next_attempt_at = None
                    log.warning("Libro %s: fase %s fallita definitivamente: %s", book.id, snap.phase, result.error)
                else:
                    delay = result.retry_delay_minutes or snap.settings["phase_retry_delay_minutes"]
                    book.status = "waiting"
                    book.next_attempt_at = now + timedelta(minutes=delay)
                    log.warning(
                        "Libro %s: fase %s fallita (tentativo %s/%s), riprovo tra %s min",
                        book.id, snap.phase, book.attempts, max_attempts, delay,
                    )
            db.commit()
            if result.ok:
                apply_pauses(db, snap.settings)  # la nuova fase potrebbe essere in pausa


worker = Worker()
