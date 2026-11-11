"""Ricerca di annunci su Vinted tramite il pacchetto vinted-lister.

Il pacchetto è sincrono (curl_cffi): queste funzioni vanno chiamate in un
thread (asyncio.to_thread). Si crea un client nuovo a ogni ricerca, così i
token reimportati con `vinted auth import` valgono subito, senza riavviare.
"""

import re
from datetime import datetime, timezone
from typing import Any

from .config import config


class VintedOpError(Exception):
    """Errore in un'operazione su Vinted. `permanent`: dati non validi, ritentare
    non serve (bisogna correggere il libro o il JSON di Claude)."""

    def __init__(self, message: str, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


# Messaggio di VintedError per una risposta HTTP: "search_items -> 401: ..."
_AUTH_ERROR = re.compile(r"-> (401|403):")


def wrap_error(prefix: str, exc: Exception) -> VintedOpError:
    msg = str(exc) or repr(exc)
    if _AUTH_ERROR.search(msg):
        msg += " — sessione Vinted scaduta? Rifai `vinted auth import`"
    return VintedOpError(f"{prefix}: {msg}"[:4000])


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def new_client():
    try:
        from vinted import VintedClient
    except ImportError as exc:
        raise VintedOpError(
            "Pacchetto vinted-lister non installato nel virtualenv del backend "
            "(pip install -e <cartella del clone>)"
        ) from exc
    try:
        return VintedClient(env_path=config.vinted_env_file or None)
    except Exception as exc:  # file dei token mancante, mercato sconosciuto, ...
        raise VintedOpError(f"Configurazione di vinted-lister non valida: {exc}") from exc


def _compact(item: dict[str, Any], host: str, item_amount) -> dict[str, Any]:
    price = item.get("price")
    currency = price.get("currency_code") if isinstance(price, dict) else None
    # svc-catalogue mette le condizioni dei libri in item_box.second_line
    condition = item.get("status") or (item.get("item_box") or {}).get("second_line") or ""
    url = item.get("url") or ""
    if url.startswith("/"):
        url = f"https://{host}{url}"
    elif not url and item.get("id"):
        url = f"https://{host}/items/{item['id']}"
    return {
        "id": item.get("id"),
        "title": item.get("title") or "",
        "price": item_amount(item),
        "currency": currency,
        "condition": condition,
        "url": url,
    }


def search_prices(text: str, categories: str, conditions: str, limit: int) -> dict[str, Any]:
    """Equivalente di `vinted search TEXT --category ... --condition ... --limit N`."""
    client = new_client()
    from vinted import Resolver
    from vinted.pricing import item_amount, price_summary

    try:
        resolver = Resolver(client)
        catalog_ids = []
        for name in _split(categories):
            cid, _ = resolver.catalog(name)
            if cid is None:
                raise VintedOpError(f"Categoria Vinted sconosciuta: {name}")
            catalog_ids.append(cid)
        status_ids = []
        for name in _split(conditions):
            sid = resolver.condition_id(name)
            if sid is None:
                raise VintedOpError(f"Condizione Vinted sconosciuta: {name}")
            status_ids.append(sid)
        items = client.search_all(text, limit=limit, catalog_ids=catalog_ids, status_ids=status_ids)
    except VintedOpError:
        raise
    except Exception as exc:
        raise wrap_error("Ricerca su Vinted non riuscita", exc) from exc

    return {
        "query": text,
        "categories": _split(categories),
        "conditions": _split(conditions),
        "searched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": price_summary(items),  # None se nessun prezzo
        "items": [_compact(it, client.host, item_amount) for it in items],
    }


def describe(result: dict[str, Any]) -> str:
    """Riga di riepilogo per lo storico delle elaborazioni."""
    items, s = result["items"], result["summary"]
    if not items:
        return f"Nessun annuncio trovato per {result['query']}"
    if not s:
        return f"{len(items)} annunci per {result['query']}, senza prezzo"
    cur = next((i["currency"] for i in items if i.get("currency")), "")
    return (
        f"{len(items)} annunci per {result['query']}: "
        f"min {s['min']:g} | mediana {s['median']:g} | max {s['max']:g} {cur}".rstrip()
    )
