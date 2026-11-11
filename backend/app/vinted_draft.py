"""Creazione della bozza di annuncio su Vinted (fase 3), tramite vinted-lister.

Equivale a `vinted draft --photo ... --title ... --description ... --isbn ...
--category ... --condition ... --price ... --attr language_book=... --no-move`.
Come la ricerca, va chiamata in un thread (il pacchetto è sincrono).
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .vinted_search import VintedOpError, new_client, wrap_error

# Segnaposto per i libri senza ISBN
NO_ISBN = "0000000000000"

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def render_description(template: str, values: dict[str, str]) -> str:
    """Sostituisce i segnaposto noti; toglie le righe in cui sono tutti vuoti."""
    lines = []
    for line in template.splitlines():
        names = [n for n in _PLACEHOLDER.findall(line) if n in values]
        if names and not any(values[n] for n in names):
            continue
        lines.append(_PLACEHOLDER.sub(lambda m: values.get(m[1], m[0]), line).rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


@dataclass
class DraftData:
    title: str
    description: str
    isbn: str
    category: str
    condition: str
    language: str
    price: float
    photos: list[Path]


def create_draft(d: DraftData) -> dict[str, Any]:
    client = new_client()
    from vinted import Resolver
    from vinted.lister import create_listing

    market = client.market
    resolver = Resolver(client)

    # --- verifiche, prima di caricare qualsiasi foto ---------------------------
    aliases = market.catalog_aliases  # nomi (minuscoli) -> id, da config/markets.json
    generic = aliases.get("libri")
    name = d.category.strip().lower()
    catalog_id = int(name) if name.isdigit() else aliases.get(name)
    allowed = ", ".join(f'"{k}"' for k in sorted(k for k, v in aliases.items() if v != generic))
    if catalog_id is None:
        raise VintedOpError(f'Categoria "{d.category}" non riconosciuta. Valori ammessi: {allowed}', permanent=True)
    if catalog_id == generic:
        raise VintedOpError(
            f'La categoria "{d.category}" è troppo generica per una bozza. Valori ammessi: {allowed}',
            permanent=True,
        )

    condition_id = resolver.condition_id(d.condition)
    if condition_id is None:
        names = ", ".join(f'"{k}"' for k in market.condition_aliases)
        raise VintedOpError(f'Condizioni "{d.condition}" non riconosciute. Valori ammessi: {names}', permanent=True)

    try:
        attributes = resolver.attributes([f"language_book={d.language}"])
    except ValueError:
        raise VintedOpError(
            f'Lingua "{d.language}" non riconosciuta da Vinted (es. Italiano, Inglese, Francese)', permanent=True
        ) from None

    missing = [p.name for p in d.photos if not p.is_file()]
    if not d.photos or missing:
        raise VintedOpError(f"Fotografie mancanti: {', '.join(missing) or 'nessuna foto'}", permanent=True)

    spec = {
        "files": [str(p) for p in d.photos],
        "title": d.title,
        "description": d.description,
        "catalog_id": catalog_id,
        "brand_id": None,
        "brand": None,
        "color_ids": [],
        "size_id": None,
        "condition_id": condition_id,
        "material_ids": [],
        "isbn": d.isbn,
        "attributes": attributes,
        "price": d.price,
        "package_size_id": 1,
    }

    # --- caricamento foto e creazione della bozza -------------------------------
    try:
        # organize=False = --no-move (le foto restano in BOOKS_DIR);
        # log=False: la bozza è registrata nel nostro DB, non in LISTINGS.md
        result = create_listing(spec, client=client, organize=False, log=False)
    except Exception as exc:
        raise wrap_error("Creazione della bozza su Vinted non riuscita", exc) from exc
    if not result.get("draft_id"):
        raise VintedOpError("Vinted non ha restituito l'id della bozza: controlla le bozze sul sito")

    return {
        "draft_id": result["draft_id"],
        "edit_url": result["edit_url"],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "title": d.title,
        "price": result["price"],
        "currency": market.currency,
        "photos": result["photos"],
        "isbn": d.isbn,
        "category": d.category,
        "catalog_id": catalog_id,
        "condition": d.condition,
        "condition_id": condition_id,
        "language": d.language,
    }


_NOT_FOUND = re.compile(r"-> 404:")


def delete_draft(draft_id: int) -> str:
    """Elimina definitivamente la bozza da Vinted (come `vinted delete --draft`).
    Se su Vinted non esiste più (già eliminata a mano) non è un errore."""
    client = new_client()
    try:
        client.delete_draft(int(draft_id))
    except Exception as exc:
        if _NOT_FOUND.search(str(exc)):
            return f"Bozza {draft_id} non trovata su Vinted (già eliminata)"
        raise wrap_error("Eliminazione della bozza su Vinted non riuscita", exc) from exc
    return f"Bozza {draft_id} eliminata da Vinted"


# --- Pubblicazione (fase 4) e approvazione (fase 5) ----------------------------

# Errori di validazione: ritentare la stessa pubblicazione non serve
_INVALID = re.compile(r"-> (400|422):")


def publish_draft(draft_id: int) -> str:
    """Chiede a Vinted di pubblicare la bozza (come `vinted publish ID`).
    Restituisce l'indirizzo pubblico che l'annuncio avrà dopo l'approvazione."""
    client = new_client()
    try:
        client.publish_draft(int(draft_id))
    except Exception as exc:
        err = wrap_error("Pubblicazione su Vinted non riuscita", exc)
        err.permanent = bool(_INVALID.search(str(exc)))
        raise err from exc
    return client.market.item_url(int(draft_id))


def item_state(draft_id: int) -> str:
    """Stato dell'annuncio dopo la richiesta di pubblicazione (come `vinted item ID`):
    "review" = ancora in approvazione (durante l'approvazione Vinted risponde 404),
    "draft" = riportato in bozza, "published" = pubblicato."""
    client = new_client()
    try:
        item = client.get_item_edit(int(draft_id))
    except Exception as exc:
        if _NOT_FOUND.search(str(exc)):
            return "review"
        raise wrap_error("Controllo dell'annuncio su Vinted non riuscito", exc) from exc
    if "is_draft" not in item:
        raise VintedOpError("Risposta di Vinted senza il campo is_draft: stato dell'annuncio sconosciuto")
    return "draft" if item["is_draft"] else "published"
