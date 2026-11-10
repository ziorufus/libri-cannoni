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


def book_name(output: str | None, title_key: str, author_key: str) -> str | None:
    """'Titolo - Autore' (o solo 'Titolo'); None se il JSON non c'è o manca il titolo."""
    data = parse_output(output)
    if data is None:
        return None
    title = _lookup(data, title_key)
    if not title:
        return None
    author = _lookup(data, author_key) if author_key else ""
    return f"{title} - {author}" if author else title


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
