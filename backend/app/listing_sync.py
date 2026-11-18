"""Allinea un libro allo stato del suo annuncio letto dal guardaroba di Vinted:
fase (pubblicato / nascosto / riservato / venduto) e prezzo. Usato da "Aggiorna"
nella scheda del libro e dal job periodico del guardaroba."""

import json
from typing import Any

from sqlalchemy.orm import Session

from .db import utcnow
from .models import Book, PhaseRun
from .phases import LISTING_PHASE_NUMBERS, LISTING_PHASES, PHASES


def item_id(book: Book) -> int | None:
    try:
        draft = json.loads(book.vinted_draft) if book.vinted_draft else None
    except ValueError:
        return None
    value = (draft or {}).get("draft_id") if isinstance(draft, dict) else None
    return int(value) if value else None


def _eur(value: float | None) -> str:
    if value is None:
        return "—"
    text = f"{value:.0f}" if value == int(value) else f"{value:.2f}".replace(".", ",")
    return f"{text} €"


def apply_listing(db: Session, book: Book, listing: dict[str, Any], worker: str) -> list[str]:
    """Applica stato e prezzo dell'annuncio al libro; restituisce le modifiche fatte
    (vuoto se era già allineato) e, se ce ne sono, le registra nello storico. Non fa commit."""
    if book.sold_manual:
        return []  # venduto segnato a mano: vale quello, non lo stato su Vinted
    changes: list[str] = []
    state = listing.get("state")
    if state is None:
        return [f"Annuncio chiuso su Vinted per un motivo sconosciuto ({listing.get('closing_action')}): stato non cambiato"]

    price = listing.get("price")
    if price is not None and price != book.price:
        changes.append(f"prezzo {_eur(book.price)} → {_eur(price)}")
        book.price = price

    phase = LISTING_PHASES[state]
    if phase != book.phase:
        old = PHASES[book.phase].label if book.phase in PHASES else f"fase {book.phase}"
        changes.append(f"{old} → {PHASES[phase].label}")
        if phase == LISTING_PHASES["sold"]:
            # La cifra incassata si propone uguale al prezzo dell'annuncio: si corregge a mano
            book.sold_at = book.sold_at or utcnow()
            if book.sold_price is None:
                book.sold_price = price if price is not None else book.price
                changes.append(f"prezzo di vendita proposto {_eur(book.sold_price)}")
        elif book.phase == LISTING_PHASES["sold"]:
            # Vendita annullata su Vinted
            changes.append(f"vendita annullata (prezzo di vendita {_eur(book.sold_price)} tolto)")
            book.sold_price = None
            book.sold_at = None
        move_to_listing_phase(book, phase)

    if changes:
        db.add(
            PhaseRun(
                book_id=book.id,
                phase=book.phase,
                attempt=0,
                worker=worker,
                started_at=utcnow(),
                finished_at=utcnow(),
                success=True,
                output="Aggiornato da Vinted: " + ", ".join(changes),
            )
        )
    return changes


def move_to_listing_phase(book: Book, phase: int) -> None:
    assert phase in LISTING_PHASE_NUMBERS
    book.phase = phase
    book.status = "idle"
    book.attempts = 0
    book.next_attempt_at = None
    book.last_error = ""
    book.force_phase = None
    book.lease_owner = None
    book.lease_until = None
