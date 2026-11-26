"""Allinea un libro allo stato del suo annuncio letto dal guardaroba di Vinted:
fase (pubblicato / nascosto / riservato / venduto) e prezzo, e dall'ordine
Vinted (la transazione della conversazione con l'acquirente) l'importo
effettivo e il pagamento (fase "Pagato" quando l'acquirente conferma la
ricezione). Usato da "Aggiorna" nella scheda del libro e dal job periodico del
guardaroba."""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from .db import utcnow
from .models import Book, PhaseRun
from .phases import LISTING_PHASE_NUMBERS, LISTING_PHASES, PAID_PHASE, PHASES, SOLD_PHASE

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


@dataclass(frozen=True)
class OrderSale:
    """Vendita letta da un ordine Vinted (vedi vinted.client.Sale), salvata nel
    libro (`vinted_draft["vinted_order"]`): il job la legge quando la conversazione
    cambia e la applica anche ai giri in cui l'annuncio non è nella pagina letta."""

    amount: float
    bundle_items: int = 1
    bundle_total: float | None = None
    completed: bool = False
    completed_at: str | None = None
    transaction_id: int | None = None
    conversation_id: int | None = None

    @property
    def is_bundle(self) -> bool:
        return self.bundle_items > 1


def remember_sale(book: Book, sale: "Sale | OrderSale") -> OrderSale:
    order = OrderSale(
        sale.amount, sale.bundle_items, sale.bundle_total, sale.completed,
        sale.completed_at, sale.transaction_id, sale.conversation_id,
    )
    draft = _draft(book)
    draft["vinted_order"] = asdict(order)
    book.vinted_draft = json.dumps(draft, ensure_ascii=False)
    return order


def stored_sale(book: Book) -> OrderSale | None:
    data = _draft(book).get("vinted_order")
    if not isinstance(data, dict):
        return None
    try:
        return OrderSale(**data)
    except TypeError:
        return None


def forget_sale(book: Book) -> None:
    draft = _draft(book)
    if draft.pop("vinted_order", None) is not None:
        book.vinted_draft = json.dumps(draft, ensure_ascii=False)


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


def _apply_sale(book: Book, sale: "Sale | OrderSale") -> str:
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


def needs_order(book: Book, listing: dict[str, Any] | None) -> bool:
    """Venduto su Vinted di cui va letto l'ordine: per l'importo effettivo (se
    manca) o per sapere se l'acquirente ha confermato la ricezione (fase Pagato)."""
    return (
        listing is not None
        and listing.get("state") == "sold"
        and not book.sold_manual
        and book.phase in LISTING_PHASE_NUMBERS  # non ancora pagato (né versato)
    )


def paid_at(book: Book) -> str | None:
    """Quando l'incasso è stato accreditato (ISO 8601), se il libro è stato pagato."""
    return _draft(book).get("paid_at")


def _date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y")
    except ValueError:
        return iso


def mark_paid(book: Book, when: str | None = None) -> str:
    """Porta un libro venduto alla fase Pagato, con la data dell'accredito (quella
    della conferma su Vinted o, se manca, adesso). Restituisce la data (gg/mm/aaaa)."""
    draft = _draft(book)
    draft["paid_at"] = when or utcnow().isoformat(timespec="seconds")
    book.vinted_draft = json.dumps(draft, ensure_ascii=False)
    move_to_listing_phase(book, PAID_PHASE)
    return _date(draft["paid_at"])


def _eur(value: float | None) -> str:
    if value is None:
        return "—"
    text = f"{value:.0f}" if value == int(value) else f"{value:.2f}".replace(".", ",")
    return f"{text} €"


def apply_listing(
    db: Session, book: Book, listing: dict[str, Any], worker: str, sale: "Sale | OrderSale | None" = None
) -> list[str]:
    """Applica stato e prezzo dell'annuncio al libro; restituisce le modifiche fatte
    (vuoto se era già allineato) e, se ce ne sono, le registra nello storico. Non fa commit.
    `sale`: vendita effettiva dall'ordine Vinted, se trovata (vedi vinted_listing.sales_since).
    `listing` con prezzo None lascia il prezzo com'è (libro venduto non letto dal guardaroba)."""
    if book.sold_manual:
        return []  # venduto segnato a mano: vale quello, non lo stato su Vinted
    if book.phase not in LISTING_PHASE_NUMBERS:
        return []  # pagato o versato: la vendita è conclusa
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
            forget_sale(book)  # l'ordine annullato non vale per un'eventuale nuova vendita
        move_to_listing_phase(book, phase)
    elif phase == LISTING_PHASES["sold"] and sale is not None and needs_sale_price(book, listing):
        # Già venduto con il prezzo provvisorio: arriva l'importo dell'ordine
        old = book.sold_price
        origin = _apply_sale(book, sale)
        if sale.amount != old or sale.is_bundle:
            changes.append(f"prezzo di vendita {_eur(old)} → {_eur(sale.amount)} ({origin})")

    if book.phase == SOLD_PHASE and sale is not None and sale.completed:
        # L'acquirente ha confermato la ricezione: Vinted accredita l'incasso
        day = mark_paid(book, sale.completed_at)
        changes.append(f"Venduto → Pagato (ricezione confermata dall'acquirente il {day})")

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
    assert phase in LISTING_PHASE_NUMBERS or phase == PAID_PHASE
    book.phase = phase
    book.status = "idle"
    book.attempts = 0
    book.next_attempt_at = None
    book.last_error = ""
    book.force_phase = None
    book.lease_owner = None
    book.lease_until = None
