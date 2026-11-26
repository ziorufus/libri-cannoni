"""Gestione libri (solo amministratori) e distribuzione delle foto."""

import asyncio
import json
from datetime import timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..claude_json import book_condition, book_isbn, book_name
from ..db import get_db, utcnow
from ..jobs import INBOX_BOOTSTRAP, load_data
from ..models import BOOK_CONDITIONS, Book, Donation, DonationBook, JobState, PhaseRun, Photo
from ..listing_sync import (
    OrderSale,
    _eur as eur,
    apply_listing,
    item_id,
    move_to_listing_phase,
    mark_paid,
    needs_order,
    needs_sale_price,
    remember_sale,
    set_sold_price_source,
    stored_sale,
)
from ..phases import (
    DONATABLE_PHASES,
    LISTING_PHASE_NUMBERS,
    LISTING_PHASES,
    MANUAL_ONLY_PHASES,
    PHASES,
    PUBLISH_PHASE,
    SOLD_PHASE,
    initial_status,
    pause_key,
    suggested_price,
    unmet_requirements,
)
from ..security import CurrentUser, require_admin
from ..settings_store import get_setting, paused_phases
from ..vinted_draft import delete_draft
from ..vinted_listing import conversation_sales, delete_listing, find_listing, sales_since, set_hidden
from ..vinted_search import VintedOpError
from ..worker import apply_pauses, load_settings
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
        "condition_manual": bool(b.condition_manual),
        "price": b.price,
        "sold_price": b.sold_price,
        "sold_at": b.sold_at,
        "sold_manual": bool(b.sold_manual),
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


def _json_column(value: str | None) -> dict | None:
    try:
        return json.loads(value) if value else None
    except ValueError:
        return None


def _blocks(b: Book, settings: dict) -> tuple[list[dict], bool]:
    """Motivi per cui il libro non può eseguire la fase attuale (calcolati ora),
    e se si può forzare la pausa: solo se la pausa è l'unico blocco, perché i
    requisiti (es. il prezzo) non si possono superare."""
    phase = PHASES.get(b.phase)
    if phase is None or phase.handler is None or b.status in ("running", "failed"):
        return [], False
    blocks = [{"kind": "requirement", "message": m} for m in unmet_requirements(b, settings)]
    manual = bool(settings.get(pause_key(b.phase)))
    auto = (settings.get("auto_pauses") or {}).get(b.phase)
    paused = manual or bool(auto)
    forced = b.force_phase == b.phase
    if paused and not forced:
        if manual:
            blocks.append({"kind": "pause", "message": f"La fase {b.phase} ({phase.label}) è in pausa per tutti i libri."})
        if auto:
            blocks.append({"kind": "pause", "message": f"Pausa automatica: {auto}"})
    return blocks, paused and not forced and not any(b["kind"] == "requirement" for b in blocks)


def _donation_of(db: Session, book_id: int) -> dict | None:
    d = db.scalar(select(Donation).join(DonationBook).where(DonationBook.book_id == book_id))
    return None if d is None else {"id": d.id, "date": d.date.isoformat(), "amount": d.amount}


def _book_detail(b: Book, db: Session) -> dict:
    settings = load_settings(db)
    blocks, can_force = _blocks(b, settings)
    return {
        **_book_summary(b),
        "blocks": blocks,
        "can_force": can_force,
        "suggested_price": suggested_price(b, settings),
        "forced": b.force_phase is not None and b.force_phase == b.phase,
        "folder": str(book_dir(b.id)),
        "claude_output": b.claude_output,
        "isbn": book_isbn(b.claude_output, get_setting(db, "json_isbn_key")),
        "vinted_prices": _json_column(b.vinted_prices),
        "vinted_draft": _json_column(b.vinted_draft),
        "donation": _donation_of(db, b.id),
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
            "blocked": "Bloccato",
            "paused": "In pausa",
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

    book = Book(
        notes=notes.strip(),
        condition=condition,
        condition_manual=condition is not None,
        created_by=cu.email,
        phase=0,
        status="ready",
    )
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
    # In euro. Conta solo se presente nella richiesta: null svuota il campo
    price: float | None = Field(default=None, ge=0, le=100000)
    # Cifra incassata (solo libri venduti); null svuota il campo
    sold_price: float | None = Field(default=None, ge=0, le=100000)


def _name_from_output(db: Session, output: str | None) -> str | None:
    return book_name(
        output,
        get_setting(db, "json_title_key"),
        get_setting(db, "json_author_key"),
        get_setting(db, "title_max_length"),
    )


def _fill_condition_from_output(db: Session, book: Book) -> None:
    if not book.condition:
        book.condition = book_condition(book.claude_output, get_setting(db, "json_condition_key"))


@router.patch("/books/{book_id}")
def update_book(
    book_id: int,
    body: BookUpdate,
    request: Request,
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
    if "price" in body.model_fields_set:
        book.price = None if body.price is None else round(body.price, 2)
    if "sold_price" in body.model_fields_set:
        if book.phase not in DONATABLE_PHASES:
            raise HTTPException(400, "Il prezzo di vendita si imposta solo per i libri venduti o pagati (non versati)")
        book.sold_price = None if body.sold_price is None else round(body.sold_price, 2)
        set_sold_price_source(book, "manual")  # non più sovrascritto dall'ordine Vinted
    if body.condition is not None:
        book.condition = _check_condition(body.condition)
        book.condition_manual = book.condition is not None
    elif body.claude_output is not None:
        _fill_condition_from_output(db, book)
    _unblock(db, book, request)
    return _book_detail(_load(db, book_id), db)


def _unblock(db: Session, book: Book, request: Request) -> None:
    """Dopo una modifica un libro bloccato torna in coda: il processo ricontrolla
    i requisiti (es. il prezzo appena inserito) e, se ci sono, lo fa ripartire."""
    if book.status == "blocked":
        book.status = "ready"
        book.last_error = ""
    db.commit()
    apply_pauses(db)
    request.app.state.worker.wake()


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
    book.condition_manual = False
    db.commit()
    return _book_detail(_load(db, book_id), db)


@router.delete("/books/{book_id}", status_code=204)
def delete_book(book_id: int, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    book = _load(db, book_id)
    if db.scalar(select(DonationBook.donation_id).where(DonationBook.book_id == book_id)):
        raise HTTPException(400, "Il libro fa parte di un versamento: elimina prima il versamento")
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
    apply_pauses(db)
    request.app.state.worker.wake()
    return _book_detail(_load(db, book_id), db)


@router.post("/books/{book_id}/force")
def force_phase(
    book_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require_admin),
):
    """Esegue la fase attuale di questo libro anche se la fase è in pausa.
    Non supera i requisiti (es. il prezzo): quelli vanno soddisfatti comunque."""
    book = _load(db, book_id)
    settings = load_settings(db)
    if book.phase not in paused_phases(settings):
        raise HTTPException(400, "Questa fase non è in pausa")
    if reasons := unmet_requirements(book, settings):
        raise HTTPException(400, "Non si può forzare: " + " ".join(reasons))
    book.force_phase = book.phase
    if book.status in ("paused", "idle"):
        book.status = "ready"
    db.commit()
    apply_pauses(db)
    request.app.state.worker.wake()
    return _book_detail(_load(db, book_id), db)


DRAFT_PHASE = 3  # fase che crea la bozza su Vinted


@router.post("/books/{book_id}/delete-draft")
def delete_vinted_draft(book_id: int, db: Session = Depends(get_db), cu: CurrentUser = Depends(require_admin)):
    """Elimina la bozza da Vinted e dal libro, e riporta il libro alla fase della
    bozza, fermo: si rilancia a mano ("Esegui la fase" o "Esegui comunque")."""
    book = _load(db, book_id)
    draft = _json_column(book.vinted_draft)
    if not draft:
        raise HTTPException(400, "Il libro non ha una bozza su Vinted")
    if book.status == "running":
        raise HTTPException(409, "Il libro è in lavorazione")
    if book.phase > PUBLISH_PHASE:
        raise HTTPException(400, "L'annuncio è in approvazione o già pubblicato su Vinted: non è più una bozza")
    started = utcnow()
    try:
        # Prima Vinted: se fallisce il libro resta com'era
        message = delete_draft(draft["draft_id"])
    except VintedOpError as exc:
        raise HTTPException(502, str(exc)) from exc
    db.add(
        PhaseRun(
            book_id=book.id,
            phase=DRAFT_PHASE,
            attempt=0,
            worker=f"eliminazione manuale ({cu.email})",
            started_at=started,
            finished_at=utcnow(),
            success=True,
            output=message,
        )
    )
    book.vinted_draft = None
    book.phase = DRAFT_PHASE
    book.status = "idle"
    book.attempts = 0
    book.next_attempt_at = None
    book.last_error = ""
    book.force_phase = None
    book.lease_owner = None
    book.lease_until = None
    db.commit()
    db.expire_all()  # ricarica anche lo storico con la nuova riga
    return _book_detail(_load(db, book_id), db)


# --- annuncio pubblicato: azioni su Vinted e aggiornamento dal guardaroba ----------

def _listed_book(db: Session, book_id: int, allowed: set[int]) -> tuple[Book, int]:
    book = _load(db, book_id)
    if book.phase not in allowed:
        labels = ", ".join(PHASES[n].label for n in sorted(allowed))
        raise HTTPException(400, f"Azione possibile solo nelle fasi: {labels}")
    if book.status == "running":
        raise HTTPException(409, "Il libro è in lavorazione")
    iid = item_id(book)
    if iid is None:
        raise HTTPException(400, "Il libro non ha un annuncio su Vinted")
    return book, iid


def _manual_run(db: Session, book: Book, cu: CurrentUser, output: str) -> None:
    db.add(
        PhaseRun(
            book_id=book.id,
            phase=book.phase,
            attempt=0,
            worker=f"azione manuale ({cu.email})",
            started_at=utcnow(),
            finished_at=utcnow(),
            success=True,
            output=output,
        )
    )


async def _read_order(db: Session, book: Book, iid: int, known: OrderSale | None) -> OrderSale | None:
    """Ordine Vinted dell'annuncio: se la conversazione è già nota la rilegge (una
    chiamata), altrimenti legge le conversazioni aggiornate dopo l'ultimo giro del
    job (senza spostarne il segnaposto: le rileggerà anche il job)."""
    gap = get_setting(db, "vinted_min_interval_seconds")
    if known is not None and known.conversation_id:
        found = await asyncio.to_thread(conversation_sales, known.conversation_id, {iid}, gap)
    else:
        since = load_data(db.get(JobState, "vinted_wardrobe")).get("inbox_since")
        if since is None and book.sold_at is not None:
            start = book.sold_at.replace(tzinfo=book.sold_at.tzinfo or timezone.utc) - INBOX_BOOTSTRAP
            since = start.isoformat(timespec="seconds")
        found, _, _ = await asyncio.to_thread(sales_since, since, {iid}, gap, 20)
    sale = found.get(iid)
    return remember_sale(book, sale) if sale is not None else known


@router.post("/books/{book_id}/vinted-refresh")
async def vinted_refresh(book_id: int, db: Session = Depends(get_db), cu: CurrentUser = Depends(require_admin)):
    """Legge l'annuncio dal guardaroba di Vinted e allinea fase e prezzo del libro."""
    book, iid = _listed_book(db, book_id, set(LISTING_PHASE_NUMBERS))
    if book.sold_manual:
        raise HTTPException(400, "Libro segnato come venduto a mano: non si aggiorna da Vinted")
    try:
        listing = await asyncio.to_thread(find_listing, iid)
    except VintedOpError as exc:
        raise HTTPException(502, str(exc)) from exc
    if listing is None:
        message = f"Annuncio {iid} non trovato nel guardaroba di Vinted (cancellato?): nessuna modifica"
    else:
        # Venduto: l'importo effettivo (offerte comprese) e la conferma della
        # ricezione (fase Pagato) sono nell'ordine, non nell'annuncio
        sale, note = stored_sale(book), ""
        if needs_order(book, listing):
            try:
                sale = await _read_order(db, book, iid, sale)
            except VintedOpError as exc:
                note = f" (ordine Vinted non letto: {exc})"
            if sale is None and not note:
                note = (
                    " (ordine non trovato: prezzo di vendita provvisorio, lo cerca il job del guardaroba)"
                    if needs_sale_price(book, listing)
                    else " (ordine non trovato: pagamento non verificato, lo cerca il job del guardaroba)"
                )
            elif sale is not None and not sale.completed:
                note = " (in attesa che l'acquirente confermi la ricezione)"
        changes = apply_listing(db, book, listing, f"Aggiorna ({cu.email})", sale)
        message = ("Aggiornato: " + ", ".join(changes) if changes else "Già allineato a Vinted") + note
        db.commit()
        db.expire_all()
    return {**_book_detail(_load(db, book_id), db), "message": message}


async def _hide(db: Session, book_id: int, cu: CurrentUser, hidden: bool) -> dict:
    src, dst = ("live", "hidden") if hidden else ("hidden", "live")
    book, iid = _listed_book(db, book_id, {LISTING_PHASES[src]})
    try:
        await asyncio.to_thread(set_hidden, iid, hidden)  # prima Vinted: se fallisce il libro resta com'era
    except VintedOpError as exc:
        raise HTTPException(502, str(exc)) from exc
    move_to_listing_phase(book, LISTING_PHASES[dst])
    _manual_run(db, book, cu, f"Annuncio {iid} {'nascosto' if hidden else 'di nuovo visibile'} su Vinted")
    db.commit()
    db.expire_all()
    return _book_detail(_load(db, book_id), db)


@router.post("/books/{book_id}/vinted-hide")
async def vinted_hide(book_id: int, db: Session = Depends(get_db), cu: CurrentUser = Depends(require_admin)):
    return await _hide(db, book_id, cu, True)


@router.post("/books/{book_id}/vinted-unhide")
async def vinted_unhide(book_id: int, db: Session = Depends(get_db), cu: CurrentUser = Depends(require_admin)):
    return await _hide(db, book_id, cu, False)


LISTING_ACTIONS = {"none": "annuncio non modificato", "hide": "annuncio nascosto", "delete": "annuncio eliminato"}


class SoldIn(BaseModel):
    sold_price: float | None = Field(default=None, ge=0, le=100000)
    # Cosa fare dell'annuncio su Vinted (es. libro venduto fuori da Vinted)
    listing_action: Literal["none", "hide", "delete"] = "delete"


@router.post("/books/{book_id}/mark-sold")
async def mark_sold(book_id: int, body: SoldIn, db: Session = Depends(get_db), cu: CurrentUser = Depends(require_admin)):
    """Segna il libro come venduto con la cifra incassata e, a scelta, nasconde o
    elimina l'annuncio su Vinted. Prima Vinted: se fallisce il libro resta com'era.
    Il venduto segnato a mano non viene più toccato dalla sincronizzazione con Vinted."""
    allowed = {LISTING_PHASES[s] for s in ("live", "hidden", "reserved")}
    book, iid = _listed_book(db, book_id, allowed)
    action = body.listing_action
    vinted_note = ""
    try:
        if action == "hide" and book.phase != LISTING_PHASES["hidden"]:
            await asyncio.to_thread(set_hidden, iid, True)
        elif action == "delete":
            vinted_note = await asyncio.to_thread(delete_listing, iid)
    except VintedOpError as exc:
        raise HTTPException(502, str(exc)) from exc
    book.sold_price = round(body.sold_price, 2) if body.sold_price is not None else book.price
    book.sold_at = utcnow()
    book.sold_manual = True
    draft = _json_column(book.vinted_draft) or {}
    draft.update(listing_action=action, listing_action_at=utcnow().isoformat(timespec="seconds"))
    book.vinted_draft = json.dumps(draft, ensure_ascii=False)
    move_to_listing_phase(book, LISTING_PHASES["sold"])
    output = f"Segnato come venduto a {eur(book.sold_price)} · {LISTING_ACTIONS[action]}"
    if vinted_note and "già eliminato" in vinted_note:
        output += " (era già stato eliminato)"
    _manual_run(db, book, cu, output)
    db.commit()
    db.expire_all()
    return _book_detail(_load(db, book_id), db)


@router.post("/books/{book_id}/mark-paid")
def mark_book_paid(book_id: int, db: Session = Depends(get_db), cu: CurrentUser = Depends(require_admin)):
    """Venduto segnato a mano: l'incasso è arrivato, il libro passa alla fase Pagato.
    (I venduti su Vinted ci passano da soli, quando l'acquirente conferma la ricezione.)"""
    book = _load(db, book_id)
    if book.phase != SOLD_PHASE:
        raise HTTPException(400, "Solo un libro venduto può essere segnato come pagato")
    if not book.sold_manual:
        raise HTTPException(
            400, "Venduto su Vinted: diventa pagato da solo quando l'acquirente conferma la ricezione (prova Aggiorna)"
        )
    mark_paid(book)
    _manual_run(db, book, cu, f"Segnato come pagato ({eur(book.sold_price)} incassati)")
    db.commit()
    db.expire_all()
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
    if book.phase in MANUAL_ONLY_PHASES or body.phase in MANUAL_ONLY_PHASES:
        raise HTTPException(
            400,
            "Le fasi Pubblicato, Nascosto, Riservato, Venduto e Pagato si cambiano solo con le azioni "
            "sull'annuncio (Nascondi, Mostra di nuovo, Segna come venduto, Segna come pagato, Aggiorna) "
            "o dagli ordini Vinted; la fase Versato dalla pagina Versamenti",
        )
    book.phase = body.phase
    book.status = initial_status(body.phase)
    book.attempts = 0
    book.next_attempt_at = None
    book.last_error = ""
    book.force_phase = None
    # Un eventuale worker in corso su questo libro scarterà il suo risultato
    book.lease_owner = None
    book.lease_until = None
    db.commit()
    apply_pauses(db)
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

