"""Stato degli annunci pubblicati, letto dal guardaroba di Vinted, e hide/unhide.

Il guardaroba (`/wardrobe/{utente}/items`, `client.my_ads`) è l'unica fonte
affidabile: contiene anche gli annunci nascosti (`is_hidden`), riservati
(`is_reserved`) e venduti (`is_closed` + `item_closing_action: "sold"`). Per un
venduto `vinted item` risponde 404, e per un nascosto non riporta `is_hidden`.
Come le altre funzioni Vinted, vanno chiamate in un thread.
"""

import re
from typing import Any

from .vinted_search import VintedOpError, new_client, wrap_error

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


# --- Prezzo effettivo di vendita ------------------------------------------------
# Il guardaroba riporta solo il prezzo di listino, anche se il libro è stato
# venduto con un'offerta: l'importo vero è negli ordini venduti, letti da
# vinted-lister (`client.sale_prices`, come `vinted sold`).
#
# Il pacchetto tiene in memoria, per ogni client, a quali annunci corrisponde una
# transazione (non cambia mai). Qui si crea un client nuovo a ogni operazione (così
# i token reimportati valgono subito): gli si passa quindi una cache condivisa.

MAX_TRANSACTIONS = 40  # transazioni nuove lette al massimo per ogni ricerca
_TX_ITEMS: dict[int, list[int]] = {}  # transaction_id -> id degli annunci


def sale_prices(item_ids: set[int]) -> dict[int, float]:
    """Importo effettivo di vendita (offerta compresa, senza le commissioni del
    compratore) degli annunci indicati. Gli annunci non trovati (o venduti in un
    lotto con altri) mancano dal risultato."""
    if not item_ids:
        return {}
    client = new_client()
    if not hasattr(client, "sale_prices"):
        raise VintedOpError(
            "La versione installata di vinted-lister non legge gli ordini venduti: "
            "aggiorna il clone (git pull) e riavvia il backend"
        )
    client._tx_items = _TX_ITEMS  # cache condivisa tra i client (vedi sopra)
    try:
        return client.sale_prices(item_ids, max_transactions=MAX_TRANSACTIONS)
    except Exception as exc:
        raise wrap_error("Lettura degli ordini venduti non riuscita", exc) from exc


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


__all__ = ["STATES", "VintedOpError", "compact", "delete_listing", "find_listing", "listing_state", "sale_prices", "set_hidden", "wardrobe_page"]
