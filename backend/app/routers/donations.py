"""Versamenti a Emergency: gestione (admin) ed elenco pubblico per la homepage.

Un versamento raccoglie libri venduti (fase 9), che passano alla fase 10
"Versato"; eliminandolo tornano alla fase 9. La ricevuta del bonifico è
un'immagine pubblica: va caricata già oscurata dove serve (IBAN, nomi, ...).
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import config
from ..db import get_db, utcnow
from ..models import Book, Donation, DonationBook, PhaseRun
from ..phases import DONATED_PHASE, SOLD_PHASE
from ..security import CurrentUser, require_admin
from ..storage import InvalidPhoto, delete_receipt, receipt_path, save_receipt, thumb_path

router = APIRouter(prefix="/api/admin/donations", tags=["donations"])
public_router = APIRouter(prefix="/api/public", tags=["public"])

MAX_RECEIPT_BYTES = 25 * 1024 * 1024


# --- serializzazione ---------------------------------------------------------------

def _eur(value: float | None) -> str:
    if value is None:
        return "—"
    text = f"{value:.0f}" if value == int(value) else f"{value:.2f}".replace(".", ",")
    return f"{text} €"


def public_book(b: Book) -> dict:
    """Dati di un libro mostrati pubblicamente (niente dati del compratore)."""
    return {
        "id": b.id,
        "title": b.description or "Libro senza titolo",
        "sold_price": b.sold_price,
        "sold_at": b.sold_at.date().isoformat() if b.sold_at else None,
        "cover_url": _cover_url(b),
    }


# Copertine dei libri venduti/versati: già pubbliche nella homepage, quindi con un
# indirizzo fisso, senza firma né scadenza (le altre foto restano private, vedi
# storage.signed_photo_url). `?v=` cambia se cambia la prima foto (cache del browser).
PUBLIC_COVER_PHASES = (SOLD_PHASE, DONATED_PHASE)


def _cover_url(b: Book) -> str | None:
    if not b.photos or b.phase not in PUBLIC_COVER_PHASES:
        return None
    return f"{config.backend_url.rstrip('/')}/api/public/books/{b.id}/cover?v={b.photos[0].id}"


def _receipt_url(d: Donation, size: str) -> str | None:
    if not d.receipt_filename:
        return None
    # ?v= cambia se la ricevuta viene sostituita (cache del browser)
    return f"{config.backend_url.rstrip('/')}/api/public/donations/{d.id}/receipt/{size}?v={d.receipt_filename}"


def _sort_books(books: list[Book]) -> list[Book]:
    return sorted(books, key=lambda b: (b.sold_at is None, -(b.sold_at.timestamp() if b.sold_at else 0), b.id))


def donation_out(d: Donation) -> dict:
    books = _sort_books([link.book for link in d.links])
    return {
        "id": d.id,
        "date": d.date.isoformat(),
        "amount": d.amount,
        "description": d.description,
        "receipt_url": _receipt_url(d, "full"),
        "receipt_thumb_url": _receipt_url(d, "thumb"),
        "books": [public_book(b) for b in books],
        "books_total": round(sum(b.sold_price or 0 for b in books), 2),
    }


def _donations(db: Session) -> list[Donation]:
    return list(
        db.scalars(
            select(Donation)
            .options(selectinload(Donation.links).selectinload(DonationBook.book).selectinload(Book.photos))
            .order_by(Donation.date.desc(), Donation.id.desc())
        )
    )


def _pending_books(db: Session) -> list[Book]:
    """Libri venduti non ancora versati."""
    books = db.scalars(select(Book).options(selectinload(Book.photos)).where(Book.phase == SOLD_PHASE)).all()
    return _sort_books(list(books))


# --- parte pubblica (nessun login) ----------------------------------------------------

@public_router.get("/donations")
def public_donations(db: Session = Depends(get_db)):
    pending = _pending_books(db)
    return {
        "pending": {
            "books": [public_book(b) for b in pending],
            "total": round(sum(b.sold_price or 0 for b in pending), 2),
        },
        "donations": [donation_out(d) for d in _donations(db)],
    }


@public_router.get("/donations/{donation_id}/receipt/{size}")
def public_receipt(donation_id: int, size: str, db: Session = Depends(get_db)):
    if size not in ("thumb", "full"):
        raise HTTPException(404)
    d = db.get(Donation, donation_id)
    path = receipt_path(donation_id, size)
    if d is None or not d.receipt_filename or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@public_router.get("/books/{book_id}/cover")
def public_cover(book_id: int, db: Session = Depends(get_db)):
    """Miniatura della prima foto, solo per i libri venduti o versati (404 per gli altri)."""
    book = db.get(Book, book_id)
    if book is None or book.phase not in PUBLIC_COVER_PHASES or not book.photos:
        raise HTTPException(404)
    path = thumb_path(book.id, book.photos[0].filename)
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


# --- parte admin ---------------------------------------------------------------------------

def _set_phase(db: Session, book: Book, phase: int, worker: str, output: str) -> None:
    book.phase = phase
    book.status = "idle"
    book.attempts = 0
    book.next_attempt_at = None
    book.last_error = ""
    book.force_phase = None
    book.lease_owner = None
    book.lease_until = None
    now = utcnow()
    db.add(
        PhaseRun(
            book_id=book.id, phase=phase, attempt=0, worker=worker,
            started_at=now, finished_at=now, success=True, output=output,
        )
    )


def _load(db: Session, donation_id: int) -> Donation:
    d = db.get(Donation, donation_id)
    if d is None:
        raise HTTPException(404, "Versamento non trovato")
    return d


@router.get("")
def list_donations(db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    pending = _pending_books(db)
    return {
        "donations": [donation_out(d) for d in _donations(db)],
        "pending_count": len(pending),
        "pending_total": round(sum(b.sold_price or 0 for b in pending), 2),
    }


@router.post("", status_code=201)
async def create_donation(
    date_: Annotated[date, Form(alias="date")],
    amount: Annotated[float, Form(ge=0, le=1_000_000)],
    book_ids: Annotated[list[int], Form()],
    receipt: Annotated[UploadFile, File()],
    description: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
    cu: CurrentUser = Depends(require_admin),
):
    """Crea il versamento con i libri venduti indicati, che passano alla fase "Versato"."""
    ids = sorted(set(book_ids))
    if not ids:
        raise HTTPException(400, "Seleziona almeno un libro venduto")
    books = db.scalars(select(Book).where(Book.id.in_(ids))).all()
    if missing := set(ids) - {b.id for b in books}:
        raise HTTPException(400, f"Libri inesistenti: {', '.join(map(str, sorted(missing)))}")
    if wrong := [b.id for b in books if b.phase != SOLD_PHASE]:
        raise HTTPException(400, f"Solo i libri venduti (fase 9) possono essere versati: #{', #'.join(map(str, wrong))}")
    if taken := db.scalars(select(DonationBook.book_id).where(DonationBook.book_id.in_(ids))).all():
        raise HTTPException(400, f"Libri già in un versamento: #{', #'.join(map(str, taken))}")
    data = await receipt.read(MAX_RECEIPT_BYTES + 1)
    if not data:
        raise HTTPException(400, "Manca la ricevuta del bonifico")
    if len(data) > MAX_RECEIPT_BYTES:
        raise HTTPException(400, "Ricevuta troppo grande (massimo 25 MB)")

    d = Donation(date=date_, amount=round(amount, 2), description=description.strip(), created_by=cu.email)
    db.add(d)
    db.flush()  # serve l'id per il file della ricevuta
    try:
        save_receipt(d.id, data)
    except (InvalidPhoto, OSError) as exc:
        db.rollback()
        delete_receipt(d.id)
        raise HTTPException(400, "La ricevuta deve essere un'immagine (JPEG, PNG o WebP)") from exc
    d.receipt_filename = f"{d.id}-{int(utcnow().timestamp())}"
    for b in books:
        db.add(DonationBook(donation_id=d.id, book_id=b.id))
        _set_phase(
            db, b, DONATED_PHASE, f"versamento ({cu.email})",
            f"Versato a Emergency con il versamento del {date_.strftime('%d/%m/%Y')} ({_eur(d.amount)})",
        )
    db.commit()
    db.expire_all()
    return donation_out(_load(db, d.id))


class DonationPatch(BaseModel):
    description: str = Field(max_length=5000)


@router.patch("/{donation_id}")
def update_donation(
    donation_id: int, body: DonationPatch, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)
):
    d = _load(db, donation_id)
    d.description = body.description.strip()
    db.commit()
    return donation_out(d)


@router.delete("/{donation_id}", status_code=204)
def delete_donation(donation_id: int, db: Session = Depends(get_db), cu: CurrentUser = Depends(require_admin)):
    """Elimina il versamento: i suoi libri tornano alla fase 9 "Venduto"."""
    d = _load(db, donation_id)
    label = f"{d.date.strftime('%d/%m/%Y')} ({_eur(d.amount)})"
    for link in d.links:
        if link.book.phase == DONATED_PHASE:
            _set_phase(db, link.book, SOLD_PHASE, f"versamento ({cu.email})", f"Versamento del {label} eliminato: torna venduto")
    db.delete(d)
    db.commit()
    delete_receipt(donation_id)
