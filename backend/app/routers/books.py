"""Gestione libri (solo amministratori) e distribuzione delle foto."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..claude_json import book_condition, book_isbn, book_name
from ..db import get_db
from ..models import BOOK_CONDITIONS, Book, Photo
from ..phases import PHASES, initial_status
from ..security import CurrentUser, require_admin
from ..settings_store import get_setting
from ..storage import (
    InvalidPhoto,
    book_dir,
    check_signature,
    delete_book_files,
    save_photos,
    signed_photo_url,
    thumb_path,
)

router = APIRouter(prefix="/api/admin", tags=["books"])
photos_router = APIRouter(prefix="/api/photos", tags=["photos"])

MAX_PHOTOS = 40
MAX_PHOTO_BYTES = 30 * 1024 * 1024


# --- serializzazione -----------------------------------------------------------

def _photo_out(p: Photo) -> dict:
    return {
        "id": p.id,
        "position": p.position,
        "filename": p.filename,
        "width": p.width,
        "height": p.height,
        "size_bytes": p.size_bytes,
        "thumb_url": signed_photo_url(p.id, "thumb"),
        "full_url": signed_photo_url(p.id, "full"),
    }


def _book_summary(b: Book) -> dict:
    phase = PHASES.get(b.phase)
    return {
        "id": b.id,
        "description": b.description,
        "condition": b.condition,
        "notes": b.notes,
        "created_by": b.created_by,
        "created_at": b.created_at,
        "updated_at": b.updated_at,
        "phase": b.phase,
        "phase_label": phase.label if phase else f"Fase {b.phase}",
        "status": b.status,
        "attempts": b.attempts,
        "next_attempt_at": b.next_attempt_at,
        "last_error": b.last_error,
        "photo_count": len(b.photos),
        "cover_url": signed_photo_url(b.photos[0].id, "thumb") if b.photos else None,
        "has_claude_output": bool(b.claude_output),
    }


def _vinted_prices(b: Book) -> dict | None:
    try:
        return json.loads(b.vinted_prices) if b.vinted_prices else None
    except ValueError:
        return None


def _book_detail(b: Book, db: Session) -> dict:
    return {
        **_book_summary(b),
        "folder": str(book_dir(b.id)),
        "claude_output": b.claude_output,
        "isbn": book_isbn(b.claude_output, get_setting(db, "json_isbn_key")),
        "vinted_prices": _vinted_prices(b),
        "photos": [_photo_out(p) for p in b.photos],
        "runs": [
            {
                "id": r.id,
                "phase": r.phase,
                "attempt": r.attempt,
                "worker": r.worker,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "success": r.success,
                "output": r.output,
                "error": r.error,
            }
            for r in b.runs
        ],
    }


def _load(db: Session, book_id: int) -> Book:
    book = db.get(Book, book_id, options=[selectinload(Book.photos), selectinload(Book.runs)])
    if book is None:
        raise HTTPException(404, "Libro non trovato")
    return book


# --- metadati ----------------------------------------------------------------------

@router.get("/meta")
def meta(db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    return {
        "photo_max_side": get_setting(db, "photo_max_side"),
        "conditions": list(BOOK_CONDITIONS),
        "phases": [
            {"number": p.number, "label": p.label, "description": p.description, "automatic": p.handler is not None}
            for p in PHASES.values()
        ],
        "statuses": {
            "ready": "In coda",
            "running": "In corso",
            "waiting": "In attesa di nuovo tentativo",
            "failed": "Fallito",
            "idle": "Fermo",
        },
    }


# --- CRUD libri -------------------------------------------------------------------

@router.get("/books")
def list_books(
    phase: int | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require_admin),
):
    q = select(Book).options(selectinload(Book.photos)).order_by(Book.id.desc())
    if phase is not None:
        q = q.where(Book.phase == phase)
    if status:
        q = q.where(Book.status == status)
    books = db.scalars(q).all()
    counts = dict(db.execute(select(Book.status, func.count()).group_by(Book.status)).all())
    return {"books": [_book_summary(b) for b in books], "counts": counts}


@router.post("/books", status_code=201)
async def create_book(
    request: Request,
    photos: Annotated[list[UploadFile], File()],
    notes: Annotated[str, Form()] = "",
    # "" = usa il valore di Claude
    condition: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
    cu: CurrentUser = Depends(require_admin),
):
    if not photos:
        raise HTTPException(400, "Serve almeno una fotografia")
    condition = _check_condition(condition)
    if len(photos) > MAX_PHOTOS:
        raise HTTPException(400, f"Al massimo {MAX_PHOTOS} fotografie per libro")

    # L'ordine dei file nel multipart è l'ordine di scatto
    images = []
    for i, f in enumerate(photos, start=1):
        data = await f.read()
        if len(data) > MAX_PHOTO_BYTES:
            raise HTTPException(400, f"La foto n. {i} è troppo grande")
        images.append(data)

    book = Book(notes=notes.strip(), condition=condition, created_by=cu.email, phase=0, status="ready")
    db.add(book)
    db.flush()  # assegna l'ID
    try:
        saved = save_photos(book.id, images, get_setting(db, "photo_max_side"))
    except InvalidPhoto as exc:
        db.rollback()
        delete_book_files(book.id)
        raise HTTPException(400, str(exc)) from exc
    except Exception:
        db.rollback()
        delete_book_files(book.id)
        raise
    for s in saved:
        db.add(Photo(book_id=book.id, **s.__dict__))
    db.commit()

    request.app.state.worker.wake()
    return _book_detail(_load(db, book.id), db)


@router.get("/books/{book_id}")
def get_book(book_id: int, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    return _book_detail(_load(db, book_id), db)


def _check_condition(value: str | None) -> str | None:
    """'' o None -> None (usa Claude); altrimenti deve essere un valore ammesso."""
    value = (value or "").strip().lower()
    if not value:
        return None
    if value not in BOOK_CONDITIONS:
        raise HTTPException(400, f"Condizioni non valide: usa {', '.join(BOOK_CONDITIONS)}")
    return value


class BookUpdate(BaseModel):
    description: str | None = None
    # "" = nessun valore (verrà preso da Claude alla prossima esecuzione della fase 1)
    condition: str | None = None
    notes: str | None = None
    claude_output: str | None = None


def _name_from_output(db: Session, output: str | None) -> str | None:
    return book_name(output, get_setting(db, "json_title_key"), get_setting(db, "json_author_key"))


def _fill_condition_from_output(db: Session, book: Book) -> None:
    if not book.condition:
        book.condition = book_condition(book.claude_output, get_setting(db, "json_condition_key"))


@router.patch("/books/{book_id}")
def update_book(
    book_id: int,
    body: BookUpdate,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require_admin),
):
    book = _load(db, book_id)
    if body.notes is not None:
        book.notes = body.notes.strip()
    if body.claude_output is not None:
        book.claude_output = body.claude_output
        # Output corretto a mano: se ora contiene titolo/autore, aggiorna il nome
        if body.description is None and (name := _name_from_output(db, book.claude_output)):
            book.description = name
    if body.description is not None:
        book.description = body.description.strip() or None
    if body.condition is not None:
        book.condition = _check_condition(body.condition)
    elif body.claude_output is not None:
        _fill_condition_from_output(db, book)
    db.commit()
    return _book_detail(_load(db, book_id), db)


@router.post("/books/{book_id}/name-from-output")
def name_from_output(book_id: int, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    """Ricalcola il nome dal JSON di Claude (es. dopo aver cambiato le etichette)."""
    book = _load(db, book_id)
    name = _name_from_output(db, book.claude_output)
    if not name:
        raise HTTPException(400, "L'output di Claude non è un JSON valido o non contiene il titolo")
    book.description = name
    db.commit()
    return _book_detail(_load(db, book_id), db)


@router.post("/books/{book_id}/condition-from-output")
def condition_from_output(book_id: int, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    """Reimposta le condizioni dal JSON di Claude. È un'azione esplicita dell'admin:
    sostituisce anche un valore già presente (a differenza della fase 1 automatica)."""
    book = _load(db, book_id)
    condition = book_condition(book.claude_output, get_setting(db, "json_condition_key"))
    if not condition:
        raise HTTPException(
            400,
            "Il JSON di Claude non contiene condizioni valide (servono esattamente "
            f"{', '.join(BOOK_CONDITIONS)} nella chiave \"{get_setting(db, 'json_condition_key')}\")",
        )
    book.condition = condition
    db.commit()
    return _book_detail(_load(db, book_id), db)


@router.delete("/books/{book_id}", status_code=204)
def delete_book(book_id: int, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    book = _load(db, book_id)
    db.delete(book)
    db.commit()
    delete_book_files(book_id)


@router.post("/books/{book_id}/retry")
def retry_book(
    book_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require_admin),
):
    """Riparte da zero con i tentativi della fase corrente."""
    book = _load(db, book_id)
    if book.status == "running":
        raise HTTPException(409, "Il libro è in lavorazione")
    book.status = initial_status(book.phase)
    book.attempts = 0
    book.next_attempt_at = None
    db.commit()
    request.app.state.worker.wake()
    return _book_detail(_load(db, book_id), db)


class PhaseReset(BaseModel):
    phase: int


@router.post("/books/{book_id}/phase")
def set_phase(
    book_id: int,
    body: PhaseReset,
    request: Request,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require_admin),
):
    """Riporta (o sposta) manualmente un libro a una fase."""
    if body.phase not in PHASES:
        raise HTTPException(400, "Fase inesistente")
    book = _load(db, book_id)
    book.phase = body.phase
    book.status = initial_status(body.phase)
    book.attempts = 0
    book.next_attempt_at = None
    book.last_error = ""
    # Un eventuale worker in corso su questo libro scarterà il suo risultato
    book.lease_owner = None
    book.lease_until = None
    db.commit()
    request.app.state.worker.wake()
    return _book_detail(_load(db, book_id), db)


# --- foto (URL firmati, senza header Authorization) ---------------------------

@photos_router.get("/{photo_id}/{size}")
def get_photo(
    photo_id: int,
    size: str,
    exp: Annotated[int, Query()],
    sig: Annotated[str, Query()],
    db: Session = Depends(get_db),
):
    if size not in ("thumb", "full") or not check_signature(photo_id, size, exp, sig):
        raise HTTPException(403, "Link non valido o scaduto")
    photo = db.get(Photo, photo_id)
    if photo is None:
        raise HTTPException(404)
    path = thumb_path(photo.book_id, photo.filename) if size == "thumb" else book_dir(photo.book_id) / photo.filename
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})

