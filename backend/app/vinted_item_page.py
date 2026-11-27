"""Lettura di un annuncio Vinted qualsiasi dalla sua pagina pubblica.

Le API JSON di Vinted non restituiscono gli annunci di altri account (né quelli
chiusi): l'unico modo è scaricare la pagina HTML (`/items/<id>-...`) e leggere
i dati di Next.js incorporati negli script `self.__next_f.push([1, "..."])`.
Ogni riga del flusso decodificato è `<id>:<JSON>`; tra questi JSON ci sono
l'oggetto dell'annuncio (titolo, prezzo, venditore), quello delle foto
(`is_closed`, `photos`) e i "plugin" con descrizione e attributi.

È un formato interno di Vinted, che può cambiare: in quel caso si ripiega sui
meta tag Open Graph (titolo, descrizione, immagine), che bastano per la pagina
pubblica. Usato da "Inserisci libro venduto su Vinted".
"""

import html
import json
import re
from dataclasses import dataclass
from typing import Any, Iterator

from .vinted_search import VintedOpError, new_client, wrap_error

ITEM_URL_RE = re.compile(r"^https?://(?:www\.)?vinted\.[a-z.]+/items/(\d+)(?:[-/?#].*)?$", re.I)
_PUSH_RE = re.compile(r"self\.__next_f\.push\((\[.*?\])\)</script>", re.S)
MAX_IMAGE_BYTES = 15 * 1024 * 1024


@dataclass
class VintedItemPage:
    item_id: int
    url: str
    title: str
    description: str = ""
    price: float | None = None  # prezzo dell'annuncio (non per forza quello di vendita)
    currency: str = ""
    seller_id: int | None = None
    is_closed: bool | None = None  # True = venduto/chiuso; None = non letto
    condition: str = ""  # es. "Ottime"
    image_url: str | None = None


def parse_item_url(url: str) -> int:
    """ID dell'annuncio da un link Vinted (https://www.vinted.it/items/123-titolo)."""
    m = ITEM_URL_RE.match(url.strip())
    if not m:
        raise VintedOpError("Link non valido: serve l'indirizzo di un annuncio Vinted (…/items/123…)", permanent=True)
    return int(m.group(1))


def _flight(page: str) -> str:
    chunks = []
    for m in _PUSH_RE.finditer(page):
        try:
            arr = json.loads(m.group(1))
        except ValueError:
            continue
        if len(arr) > 1 and isinstance(arr[1], str):
            chunks.append(arr[1])
    return "".join(chunks)


def _dicts(value: Any) -> Iterator[dict]:
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _dicts(v)
    elif isinstance(value, list):
        for v in value:
            yield from _dicts(v)


def _flight_objects(page: str) -> Iterator[dict]:
    for line in _flight(page).split("\n"):
        _, sep, rest = line.partition(":")
        if not sep or not rest[:1] in ("[", "{"):
            continue
        try:
            data = json.loads(rest)
        except ValueError:
            continue
        yield from _dicts(data)


def _meta(page: str, prop: str) -> str:
    m = re.search(rf'<meta[^>]+(?:property|name)="{re.escape(prop)}"[^>]*content="([^"]*)"', page)
    return html.unescape(m.group(1)).strip() if m else ""


def _money(value: Any) -> float | None:
    try:
        return round(float(str(value).replace(",", ".")), 2)
    except (TypeError, ValueError):
        return None


def _photo_url(photo: dict) -> str | None:
    return photo.get("full_size_url") or photo.get("url")


def parse_item_page(page: str, item_id: int, url: str) -> VintedItemPage:
    sid = str(item_id)
    item = VintedItemPage(item_id=item_id, url=url, title="")
    photos: list[dict] = []
    for d in _flight_objects(page):
        if str(d.get("id")) == sid and isinstance(d.get("title"), str):
            item.title = item.title or d["title"].strip()
            if isinstance(d.get("price"), dict) and item.price is None:
                item.price = _money(d["price"].get("amount"))
                item.currency = d["price"].get("currency_code") or ""
            if d.get("seller_id") and item.seller_id is None:
                item.seller_id = int(d["seller_id"])
        if str(d.get("item_id")) == sid:
            if isinstance(d.get("is_closed"), bool) and item.is_closed is None:
                item.is_closed = d["is_closed"]
        if isinstance(d.get("photos"), list) and not photos and str(d.get("item_id", d.get("id"))) == sid:
            photos = [p for p in d["photos"] if isinstance(p, dict) and _photo_url(p)]
        if d.get("code") == "status" and isinstance(d.get("data"), dict) and not item.condition:
            item.condition = str(d["data"].get("value") or "").strip()
        if d.get("name") == "description" and isinstance(d.get("data"), dict) and not item.description:
            item.description = str(d["data"].get("description") or "").strip()

    if photos:
        main = next((p for p in photos if p.get("is_main")), photos[0])
        item.image_url = _photo_url(main)

    # Ripieghi: meta tag Open Graph
    if not item.title:
        item.title = re.sub(r"\s*\|\s*Vinted\s*$", "", _meta(page, "og:title"))
    if not item.description:
        desc = _meta(page, "og:description")
        prefix = f"{item.title} - "
        item.description = desc[len(prefix):] if item.title and desc.startswith(prefix) else desc
    if not item.image_url:
        item.image_url = _meta(page, "og:image") or None
    return item


def fetch_item(url: str) -> VintedItemPage:
    """Scarica e legge la pagina dell'annuncio (con la sessione di vinted-lister:
    stessi cookie e stesso intervallo minimo tra le richieste)."""
    item_id = parse_item_url(url)
    client = new_client()
    page_url = f"https://{client.host}/items/{item_id}"
    try:
        r = client.get(page_url, timeout=30, headers={"accept": "text/html,application/xhtml+xml"})
    except Exception as exc:
        raise wrap_error("Lettura della pagina Vinted", exc) from exc
    if r.status_code == 404:
        raise VintedOpError(f"Annuncio {item_id} non trovato su Vinted (cancellato?)", permanent=True)
    if r.status_code != 200:
        raise VintedOpError(f"Lettura della pagina Vinted: risposta HTTP {r.status_code}")
    item = parse_item_page(r.text, item_id, page_url)
    if not item.title:
        raise VintedOpError("Pagina Vinted letta, ma senza titolo: forse Vinted ha cambiato il formato", permanent=True)
    return item


def fetch_image(url: str) -> bytes:
    client = new_client()
    try:
        r = client.get(url, timeout=60, headers={"accept": "image/*"})
    except Exception as exc:
        raise wrap_error("Download della foto da Vinted", exc) from exc
    if r.status_code != 200 or not r.content:
        raise VintedOpError(f"Download della foto da Vinted: risposta HTTP {r.status_code}")
    if len(r.content) > MAX_IMAGE_BYTES:
        raise VintedOpError("La foto su Vinted è troppo grande", permanent=True)
    return r.content
