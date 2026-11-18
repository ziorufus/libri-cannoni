"""Stato degli annunci pubblicati, letto dal guardaroba di Vinted, e hide/unhide.

Il guardaroba (`/wardrobe/{utente}/items`, `client.my_ads`) è l'unica fonte
affidabile: contiene anche gli annunci nascosti (`is_hidden`), riservati
(`is_reserved`) e venduti (`is_closed` + `item_closing_action: "sold"`). Per un
venduto `vinted item` risponde 404, e per un nascosto non riporta `is_hidden`.
Come le altre funzioni Vinted, vanno chiamate in un thread.
"""

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


__all__ = ["STATES", "VintedOpError", "compact", "find_listing", "listing_state", "set_hidden", "wardrobe_page"]
