"""Stato degli annunci pubblicati, letto dal guardaroba di Vinted, e hide/unhide.

Il guardaroba (`/wardrobe/{utente}/items`, `client.my_ads`) è l'unica fonte
affidabile: contiene anche gli annunci nascosti (`is_hidden`), riservati
(`is_reserved`) e venduti (`is_closed` + `item_closing_action: "sold"`). Per un
venduto `vinted item` risponde 404, e per un nascosto non riporta `is_hidden`.
Come le altre funzioni Vinted, vanno chiamate in un thread.
"""

import re
from typing import TYPE_CHECKING, Any

from .vinted_search import VintedOpError, new_client, wrap_error

if TYPE_CHECKING:
    from vinted.client import Sale

PER_PAGE = 96  # annunci per pagina del guardaroba (il massimo che usiamo)
MAX_PAGES = 30

# Stato su Vinted -> etichetta (le fasi sono in phases.LISTING_PHASES)
STATES = ("live", "hidden", "reserved", "sold")


def _price(item: dict[str, Any]) -> float | None:
    price = item.get("price")
    amount = price.get("amount") if isinstance(price, dict) else price
    try:
        return round(float(amount), 2)
    except (TypeError, ValueError):
        return None


def listing_state(item: dict[str, Any]) -> str | None:
    """live | hidden | reserved | sold; None se chiuso per un motivo sconosciuto."""
    if item.get("is_closed"):
        return "sold" if item.get("item_closing_action") == "sold" else None
    if item.get("is_reserved"):
        return "reserved"
    if item.get("is_hidden"):
        return "hidden"
    return "live"


def compact(item: dict[str, Any]) -> dict[str, Any]:
    price = item.get("price")
    return {
        "id": item.get("id"),
        "state": listing_state(item),
        "closing_action": item.get("item_closing_action"),
        "price": _price(item),
        "currency": price.get("currency_code") if isinstance(price, dict) else None,
    }


def wardrobe_page(page: int) -> list[dict[str, Any]]:
    """Una pagina del guardaroba (al massimo PER_PAGE annunci), in forma compatta."""
    client = new_client()
    try:
        items = client.my_ads(page=page, per_page=PER_PAGE)
    except Exception as exc:
        raise wrap_error("Lettura del guardaroba Vinted non riuscita", exc) from exc
    return [compact(it) for it in items]


def find_listing(item_id: int) -> dict[str, Any] | None:
    """Cerca un annuncio nel guardaroba, pagina per pagina; None se non c'è."""
    client = new_client()
    for page in range(1, MAX_PAGES + 1):
        try:
            items = client.my_ads(page=page, per_page=PER_PAGE)
        except Exception as exc:
            raise wrap_error("Lettura del guardaroba Vinted non riuscita", exc) from exc
        for it in items:
            if it.get("id") == item_id:
                return compact(it)
        if len(items) < PER_PAGE:
            return None
    return None


def set_hidden(item_id: int, hidden: bool) -> None:
    """Nasconde (True) o mostra di nuovo (False) un annuncio pubblicato."""
    client = new_client()
    try:
        client.set_hidden(int(item_id), hidden)
    except Exception as exc:
        what = "nascondere" if hidden else "mostrare di nuovo"
        raise wrap_error(f"Vinted non ha permesso di {what} l'annuncio", exc) from exc


# --- Vendite effettive (importo e pagamento) -------------------------------------
# Il guardaroba riporta solo il prezzo di listino, anche se il libro è stato
# venduto con un'offerta: l'importo vero e la conferma della ricezione (che fa
# accreditare l'incasso) sono nella transazione della conversazione con
# l'acquirente, letta da vinted-lister (`client.sales_since`, come `vinted sold`).
# Ogni evento dell'ordine (venduto, spedito, consegnato, ricezione confermata)
# aggiorna la conversazione: basta leggere quelle aggiornate dall'ultima volta.
# Un lotto (più annunci comprati insieme) ha un solo importo: il pacchetto lo
# divide tra gli annunci in proporzione ai prezzi di listino.
#
# Il pacchetto tiene in memoria, per ogni client, quali annunci (e a che prezzo di
# listino) contiene una transazione (non cambia mai). Qui si crea un client nuovo a
# ogni operazione (così i token reimportati valgono subito): gli si passa quindi
# una cache condivisa.

MAX_CONVERSATIONS = 40  # conversazioni lette al massimo a ogni giro (le altre al giro dopo)
_TX_ITEMS: dict[int, list[tuple[int, float | None]]] = {}  # transaction_id -> (id annuncio, prezzo di listino)


def _orders_client(min_interval: float):
    client = new_client()
    if not hasattr(client, "sales_since"):
        raise VintedOpError(
            "La versione installata di vinted-lister non legge gli ordini dalle conversazioni: "
            "aggiorna il clone (git pull) e riavvia il backend"
        )
    client._tx_items = _TX_ITEMS  # cache condivisa tra i client (vedi sopra)
    client.min_interval = max(0.0, float(min_interval))
    return client


def sales_since(
    since: str | None, wanted: set[int], min_interval: float, max_conversations: int = MAX_CONVERSATIONS
) -> tuple[dict[int, "Sale"], str | None, bool]:
    """Vendite degli annunci `wanted` nelle conversazioni aggiornate dopo `since`
    (ISO 8601): per annuncio `amount` (offerta compresa, senza le commissioni del
    compratore; per un lotto la quota dell'annuncio), `bundle_items`,
    `bundle_total`, `completed` (ricezione confermata), `completed_at`,
    `conversation_id`. Restituisce anche il nuovo `since` e se restano
    conversazioni da leggere. Tra una chiamata a Vinted e l'altra passano almeno
    `min_interval` secondi."""
    client = _orders_client(min_interval)
    try:
        return client.sales_since(since, wanted, max_conversations=max_conversations)
    except Exception as exc:
        raise wrap_error("Lettura degli ordini venduti non riuscita", exc) from exc


def conversation_sales(conversation_id: int, wanted: set[int], min_interval: float) -> dict[int, "Sale"]:
    """Rilegge la vendita di una conversazione già nota (vedi sales_since)."""
    client = _orders_client(min_interval)
    try:
        return client.conversation_sales(conversation_id, wanted)
    except Exception as exc:
        raise wrap_error("Lettura dell'ordine venduto non riuscita", exc) from exc


_NOT_FOUND = re.compile(r"-> 404:")


def delete_listing(item_id: int) -> str:
    """Elimina definitivamente un annuncio pubblicato (come `vinted delete ID`).
    Se non esiste più (404) non è un errore."""
    client = new_client()
    try:
        client.delete_item(int(item_id))
    except Exception as exc:
        if _NOT_FOUND.search(str(exc)):
            return f"Annuncio {item_id} non trovato su Vinted (già eliminato)"
        raise wrap_error("Vinted non ha permesso di eliminare l'annuncio", exc) from exc
    return f"Annuncio {item_id} eliminato da Vinted"


__all__ = ["STATES", "VintedOpError", "compact", "delete_listing", "find_listing", "conversation_sales", "listing_state", "sales_since", "set_hidden", "wardrobe_page"]
