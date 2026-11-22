"""Allinea un libro allo stato del suo annuncio letto dal guardaroba di Vinted:
fase (pubblicato / nascosto / riservato / venduto) e prezzo. Usato da "Aggiorna"
nella scheda del libro e dal job periodico del guardaroba."""

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from .db import utcnow
from .models import Book, PhaseRun
from .phases import LISTING_PHASE_NUMBERS, LISTING_PHASES, PHASES

if TYPE_CHECKING:
    from vinted.client import Sale


def item_id(book: Book) -> int | None:
    try:
        draft = json.loads(book.vinted_draft) if book.vinted_draft else None
    except ValueError:
        return None
    value = (draft or {}).get("draft_id") if isinstance(draft, dict) else None
    return int(value) if value else None


def _draft(book: Book) -> dict[str, Any]:
    try:
        draft = json.loads(book.vinted_draft) if book.vinted_draft else {}
    except ValueError:
        return {}
    return draft if isinstance(draft, dict) else {}


def set_sold_price_source(book: Book, source: str | None, bundle: dict[str, Any] | None = None) -> None:
    """Da dove viene il prezzo di vendita: "order" (ordine Vinted, offerta compresa),
    "bundle" (quota di un ordine Vinted con più annunci, divisa in proporzione ai
    prezzi di listino), "listing" (prezzo dell'annuncio, provvisorio), "manual"
    (corretto a mano). Per "bundle", `bundle` = {"items": annunci, "total": importo del lotto}."""
    draft = _draft(book)
    draft["sold_price_source"] = source
    if bundle:
        draft["sold_bundle"] = bundle
    else:
        draft.pop("sold_bundle", None)
    book.vinted_draft = json.dumps(draft, ensure_ascii=False)


def _apply_sale(book: Book, sale: "Sale") -> str:
    """Imposta il prezzo di vendita dall'ordine Vinted; restituisce da dove viene (per lo storico)."""
    book.sold_price = sale.amount
    if sale.is_bundle:
        set_sold_price_source(book, "bundle", {"items": sale.bundle_items, "total": sale.bundle_total})
        return f"quota di un lotto Vinted di {sale.bundle_items} articoli venduto a {_eur(sale.bundle_total)}"
    set_sold_price_source(book, "order")
    return "dall'ordine Vinted"


def needs_sale_price(book: Book, listing: dict[str, Any] | None) -> bool:
    """Venduto su Vinted di cui non si conosce ancora l'importo effettivo."""
    return (
        listing is not None
        and listing.get("state") == "sold"
        and not book.sold_manual
        and _draft(book).get("sold_price_source") not in ("order", "bundle", "manual")
    )


def _eur(value: float | None) -> str:
    if value is None:
        return "—"
    text = f"{value:.0f}" if value == int(value) else f"{value:.2f}".replace(".", ",")
    return f"{text} €"


def apply_listing(
    db: Session, book: Book, listing: dict[str, Any], worker: str, sale: "Sale | None" = None
) -> list[str]:
    """Applica stato e prezzo dell'annuncio al libro; restituisce le modifiche fatte
    (vuoto se era già allineato) e, se ce ne sono, le registra nello storico. Non fa commit.
    `sale`: vendita effettiva dall'ordine Vinted, se trovata (vedi vinted_listing.sales)."""
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
            book.sold_at = book.sold_at or utcnow()
            if sale is not None:
                origin = _apply_sale(book, sale)
                changes.append(f"prezzo di vendita {_eur(sale.amount)} ({origin})")
            elif book.sold_price is None:
                # Ordine non ancora trovato: provvisorio il prezzo dell'annuncio, si riprova ai giri successivi
                book.sold_price = price if price is not None else book.price
                set_sold_price_source(book, "listing")
                changes.append(f"prezzo di vendita provvisorio {_eur(book.sold_price)} (prezzo dell'annuncio)")
        elif book.phase == LISTING_PHASES["sold"]:
            # Vendita annullata su Vinted
            changes.append(f"vendita annullata (prezzo di vendita {_eur(book.sold_price)} tolto)")
            book.sold_price = None
            book.sold_at = None
            set_sold_price_source(book, None)
        move_to_listing_phase(book, phase)
    elif phase == LISTING_PHASES["sold"] and sale is not None and needs_sale_price(book, listing):
        # Già venduto con il prezzo provvisorio: arriva l'importo dell'ordine
        old = book.sold_price
        origin = _apply_sale(book, sale)
        if sale.amount != old or sale.is_bundle:
            changes.append(f"prezzo di vendita {_eur(old)} → {_eur(sale.amount)} ({origin})")

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
