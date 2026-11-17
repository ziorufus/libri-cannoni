"""Lettura del JSON prodotto da Claude (nome, condizioni, ISBN del libro)."""

import json
import re
from typing import Any

from .models import BOOK_CONDITIONS

# ```json ... ```  oppure  ``` ... ```  (anche senza a capo)
_FENCE = re.compile(r"^```[A-Za-z0-9_-]*[ \t]*\n?(.*?)\n?[ \t]*```$", re.DOTALL)


def parse_output(text: str | None) -> dict | None:
    """Restituisce il JSON (oggetto) contenuto nell'output, o None se non è valido."""
    if not text:
        return None
    text = text.strip()
    m = _FENCE.match(text)
    if m:
        text = m.group(1).strip()
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _lookup(data: dict, key: str) -> str:
    """Valore di `key` come testo; `key` può essere un percorso (es. "libro.titolo")."""
    value: Any = data
    for part in key.split("."):
        if not isinstance(value, dict):
            return ""
        value = value.get(part)
    if isinstance(value, list):  # es. più autori
        value = ", ".join(str(v).strip() for v in value if v not in (None, ""))
    if value is None or isinstance(value, (dict, bool)):
        return ""
    return str(value).strip()


def book_condition(output: str | None, key: str) -> str | None:
    """Condizioni dal JSON, solo se il valore è esattamente uno di quelli ammessi
    (maiuscole/minuscole e spazi esterni non contano); altrimenti None."""
    data = parse_output(output)
    if data is None or not key:
        return None
    value = _lookup(data, key).lower()
    return value if value in BOOK_CONDITIONS else None


ELLIPSIS = "…"
_PUNCT = " ,.;:-–—…!?'\"«»"


def _trim(text: str, limit: int) -> str:
    """I primi `limit` caratteri di `text`, possibilmente tagliati tra due parole,
    senza punteggiatura finale."""
    if len(text) <= limit:
        return text.rstrip(_PUNCT)
    cut = text[:limit]
    space = cut.rfind(" ")
    if space >= limit * 2 // 3:  # non si perde troppo: si taglia sulla parola
        cut = cut[:space]
    return cut.rstrip(_PUNCT)


def _cut(text: str, limit: int) -> str:
    """Accorcia `text` a `limit` caratteri, puntini finali compresi."""
    if len(text) <= limit:
        return text
    return _trim(text, limit - 1) + ELLIPSIS if limit > 1 else ELLIPSIS[:limit]


def _shorten_title(title: str, limit: int) -> str:
    """Accorcia il titolo a `limit` caratteri mettendo i puntini in mezzo e tenendo
    l'ultima parola ("Storia della letteratura… superiori"): Vinted rifiuta i
    titoli con troppi simboli di fila, come "… -" prima dell'autore."""
    if len(title) <= limit:
        return title
    words = title.split()
    last = words[-1].strip(_PUNCT) if len(words) > 1 else ""
    room = limit - len(last) - 2  # "… " prima dell'ultima parola
    head = _trim(" ".join(words[:-1]), room) if last and room >= 8 else ""
    if not head:  # una parola sola, o ultima parola troppo lunga: taglio senza puntini
        return _trim(title, limit)
    return f"{head}{ELLIPSIS} {last}"


def book_name(output: str | None, title_key: str, author_key: str, max_length: int | None = None) -> str | None:
    """'Titolo - Autore' (o solo 'Titolo'); None se il JSON non c'è o manca il titolo.

    Con `max_length` il risultato non supera quei caratteri: si accorcia il titolo
    (l'autore resta intero); se nemmeno così ci sta, si accorcia tutto."""
    data = parse_output(output)
    if data is None:
        return None
    title = _lookup(data, title_key)
    if not title:
        return None
    author = _lookup(data, author_key) if author_key else ""
    suffix = f" - {author}" if author else ""
    if not max_length or len(title) + len(suffix) <= max_length:
        return title + suffix
    room = max_length - len(suffix)
    if room >= 10:  # resta spazio per un titolo riconoscibile
        return _shorten_title(title, room) + suffix
    return _cut(title + suffix, max_length)


_ISBN_CHUNK = re.compile(r"[0-9Xx][0-9Xx \t-]{8,}[0-9Xx]")


def book_isbn(output: str | None, key: str) -> str | None:
    """ISBN dal JSON, ridotto a sole cifre (più l'eventuale X finale degli ISBN-10).

    Se il valore contiene più codici (es. "978-..., 88-..." o un elenco) prende il
    primo lungo 10 o 13 caratteri; None se non c'è un ISBN plausibile.
    """
    data = parse_output(output)
    if data is None or not key:
        return None
    for chunk in _ISBN_CHUNK.findall(_lookup(data, key)):
        # il pezzo intero ("978 88 04 ...") oppure, se sono più codici separati
        # da spazi, ciascuno di essi
        for candidate in (chunk, *chunk.split()):
            digits = re.sub(r"[ \t-]", "", candidate).upper()
            if len(digits) in (10, 13) and "X" not in digits[:-1]:
                return digits
    return None


def claude_value(output: str | None, key: str) -> str:
    """Valore di `key` nel JSON di Claude come testo ("" se manca o il JSON non è valido)."""
    data = parse_output(output)
    if data is None or not key:
        return ""
    return _lookup(data, key)


_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def claude_number(output: str | None, key: str) -> float | None:
    """Primo numero nel valore di `key` ("12", 12, "12,50 €", "circa 12 euro"), o None."""
    m = _NUMBER.search(claude_value(output, key))
    return float(m.group().replace(",", ".")) if m else None
