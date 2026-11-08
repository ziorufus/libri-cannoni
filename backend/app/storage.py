"""Gestione delle cartelle dei libri: <BOOKS_DIR>/<id>/01.jpg, 02.jpg, ...

Le miniature stanno in <BOOKS_DIR>/.thumbs/<id>/ per lasciare la cartella del
libro con le sole foto originali (è la cartella da cui viene lanciato Claude).
"""

import hashlib
import hmac
import io
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from .config import config

THUMB_SIZE = (480, 480)
URL_TTL_SECONDS = 6 * 3600
# MPO (Multi-Picture Object) è un JPEG con altre immagini accodate (es. mappa di
# profondità): lo producono alcuni telefoni Android/Samsung. Si legge come JPEG.
EXT_BY_FORMAT = {"JPEG": "jpg", "MPO": "jpg", "PNG": "png", "WEBP": "webp"}


def book_dir(book_id: int) -> Path:
    return config.books_dir / str(book_id)


def thumbs_dir(book_id: int) -> Path:
    return config.thumbs_dir / str(book_id)


@dataclass
class SavedPhoto:
    position: int
    filename: str
    width: int
    height: int
    size_bytes: int


class InvalidPhoto(ValueError):
    pass


def inspect_image(data: bytes, index: int) -> tuple[str, int, int]:
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format or ""
            img = ImageOps.exif_transpose(img)
            width, height = img.size
    except (UnidentifiedImageError, OSError) as exc:
        raise InvalidPhoto(f"La foto n. {index} non è un'immagine valida") from exc
    if fmt not in EXT_BY_FORMAT:
        raise InvalidPhoto(f"La foto n. {index} ha un formato non supportato ({fmt or '?'})")
    return EXT_BY_FORMAT[fmt], width, height


def fit_max_side(data: bytes, max_side: int) -> bytes:
    """Ridimensiona a `max_side` pixel sul lato lungo, se serve (JPEG, qualità 90).

    Le foto già entro il limite restano byte per byte come sono arrivate.
    """
    with Image.open(io.BytesIO(data)) as img:
        if max(img.size) <= max_side:
            return data
        img = ImageOps.exif_transpose(img)  # l'orientamento EXIF va applicato prima
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img.convert("RGB").save(out, "JPEG", quality=90, optimize=True)
        return out.getvalue()


def save_photos(book_id: int, images: list[bytes], max_side: int) -> list[SavedPhoto]:
    """Salva le foto (ridotte a `max_side` se più grandi) e genera le miniature."""
    target = book_dir(book_id)
    thumbs = thumbs_dir(book_id)
    target.mkdir(parents=True, exist_ok=False)
    thumbs.mkdir(parents=True, exist_ok=True)
    digits = max(2, len(str(len(images))))
    saved = []
    for position, data in enumerate(images, start=1):
        inspect_image(data, position)  # valida prima di elaborare
        data = fit_max_side(data, max_side)
        ext, width, height = inspect_image(data, position)
        filename = f"{position:0{digits}d}.{ext}"
        (target / filename).write_bytes(data)
        _make_thumb(data, thumbs / f"{position:0{digits}d}.jpg")
        saved.append(SavedPhoto(position, filename, width, height, len(data)))
    return saved


def _make_thumb(data: bytes, dest: Path) -> None:
    with Image.open(io.BytesIO(data)) as img:
        img = ImageOps.exif_transpose(img)
        img.thumbnail(THUMB_SIZE)
        img.convert("RGB").save(dest, "JPEG", quality=80)


def thumb_path(book_id: int, filename: str) -> Path:
    return thumbs_dir(book_id) / (Path(filename).stem + ".jpg")


def delete_book_files(book_id: int) -> None:
    shutil.rmtree(book_dir(book_id), ignore_errors=True)
    shutil.rmtree(thumbs_dir(book_id), ignore_errors=True)


# --- URL firmati: le <img> non possono mandare l'header Authorization -------

def _signature(photo_id: int, size: str, exp: int) -> str:
    msg = f"{photo_id}:{size}:{exp}".encode()
    return hmac.new(config.secret_key.encode(), msg, hashlib.sha256).hexdigest()[:32]


def signed_photo_url(photo_id: int, size: str) -> str:
    # Scadenza arrotondata all'ora: URL stabili => il browser può fare cache.
    exp = (int(time.time()) // 3600 + 1) * 3600 + URL_TTL_SECONDS
    sig = _signature(photo_id, size, exp)
    return f"{config.backend_url.rstrip('/')}/api/photos/{photo_id}/{size}?exp={exp}&sig={sig}"


def check_signature(photo_id: int, size: str, exp: int, sig: str) -> bool:
    if exp < time.time():
        return False
    return hmac.compare_digest(_signature(photo_id, size, exp), sig)
