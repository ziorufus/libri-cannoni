"""Impostazioni modificabili dagli amministratori, salvate nella tabella `settings`."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from .models import Setting

DEFAULT_CLAUDE_PROMPT = """\
Nella cartella corrente ci sono le fotografie di un libro usato che verrà ceduto \
in cambio di un'offerta per Emergency. Le fotografie, nell'ordine in cui sono state \
scattate, sono: {foto}

Esamina tutte le fotografie (copertina, retro, costa, frontespizio, colophon, \
eventuali difetti) e ricava tutte le informazioni possibili sul libro.

Note aggiuntive fornite da chi ha fotografato il libro:
{note}

Rispondi SOLO con un oggetto JSON, senza altro testo, con queste chiavi:
- "titolo": titolo (e sottotitolo)
- "autore": autore/i
- "curatore", "traduttore"
- "editore", "collana", "anno", "edizione"
- "isbn": se visibile
- "lingua", "formato", "legatura", "pagine"
- "stato": stato di conservazione, con i difetti visibili
- "descrizione": breve descrizione del contenuto, adatta a un annuncio

Se un'informazione non è ricavabile usa null invece di inventarla. \
Scrivi i testi in italiano."""


@dataclass(frozen=True)
class SettingDef:
    key: str
    label: str
    type: str  # "int" | "str" | "text"
    default: Any
    help: str = ""
    min: int | None = None
    max: int | None = None
    group: str = "Generale"


DEFINITIONS: list[SettingDef] = [
    SettingDef(
        "photo_max_side",
        "Lato massimo delle foto (pixel)",
        "int",
        2048,
        "Le foto con il lato lungo più grande vengono ridimensionate (prima dell'invio dal "
        "telefono e comunque dal server). Vale per i libri inseriti da qui in avanti.",
        min=640,
        max=12000,
        group="Fotografie",
    ),
    SettingDef(
        "worker_interval_seconds",
        "Intervallo di controllo (secondi)",
        "int",
        30,
        "Ogni quanti secondi il processo in background cerca libri da far avanzare.",
        min=5,
        max=3600,
        group="Processo in background",
    ),
    SettingDef(
        "phase_max_attempts",
        "Numero massimo di tentativi",
        "int",
        3,
        "Dopo questo numero di tentativi falliti il libro si ferma nello stato \"fallito\".",
        min=1,
        max=100,
        group="Processo in background",
    ),
    SettingDef(
        "phase_retry_delay_minutes",
        "Attesa prima di ritentare (minuti)",
        "int",
        10,
        "Dopo un tentativo fallito, quanto aspettare prima di riprovare.",
        min=0,
        max=24 * 60,
        group="Processo in background",
    ),
    SettingDef(
        "claude_timeout_seconds",
        "Timeout chiamata a Claude (secondi)",
        "int",
        600,
        "Oltre questo tempo la chiamata a `claude -p` viene interrotta e conta come fallita.",
        min=30,
        max=3600,
        group="Fase 1 · Claude",
    ),
    SettingDef(
        "claude_extra_args",
        "Argomenti aggiuntivi per `claude -p`",
        "str",
        "--allowedTools Read",
        "Passati dopo `claude -p`. `--allowedTools Read` permette a Claude di leggere le foto.",
        group="Fase 1 · Claude",
    ),
    SettingDef(
        "claude_prompt",
        "Prompt per Claude",
        "text",
        DEFAULT_CLAUDE_PROMPT,
        "Segnaposto disponibili: {foto} (elenco dei file in ordine) e {note} (note del libro).",
        group="Fase 1 · Claude",
    ),
    SettingDef(
        "json_title_key",
        "Etichetta JSON del titolo",
        "str",
        "titolo",
        "Chiave del JSON di Claude che contiene il titolo. Ammessi percorsi (es. libro.titolo).",
        group="Nome del libro",
    ),
    SettingDef(
        "json_author_key",
        "Etichetta JSON dell'autore",
        "str",
        "autore",
        "Chiave del JSON di Claude che contiene l'autore (se è un elenco, gli autori vengono "
        "separati da virgole). Il nome del libro diventa \"Titolo - Autore\".",
        group="Nome del libro",
    ),
]

BY_KEY = {d.key: d for d in DEFINITIONS}


def _parse(d: SettingDef, raw: Any) -> Any:
    if d.type == "int":
        value = int(raw)
        if d.min is not None and value < d.min:
            raise ValueError(f"{d.label}: il valore minimo è {d.min}")
        if d.max is not None and value > d.max:
            raise ValueError(f"{d.label}: il valore massimo è {d.max}")
        return value
    return str(raw)


def seed_defaults(db: Session) -> None:
    existing = {s.key for s in db.query(Setting.key)}
    for d in DEFINITIONS:
        if d.key not in existing:
            db.add(Setting(key=d.key, value=str(d.default)))
    db.commit()


def get_setting(db: Session, key: str) -> Any:
    d = BY_KEY[key]
    row = db.get(Setting, key)
    if row is None:
        return d.default
    try:
        return _parse(d, row.value)
    except ValueError:
        return d.default


def all_settings(db: Session) -> list[dict]:
    return [
        {
            "key": d.key,
            "label": d.label,
            "type": d.type,
            "help": d.help,
            "min": d.min,
            "max": d.max,
            "group": d.group,
            "default": d.default,
            "value": get_setting(db, d.key),
        }
        for d in DEFINITIONS
    ]


def update_settings(db: Session, values: dict[str, Any]) -> None:
    parsed = {}
    for key, raw in values.items():
        if key not in BY_KEY:
            raise ValueError(f"Impostazione sconosciuta: {key}")
        try:
            parsed[key] = _parse(BY_KEY[key], raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(str(exc) or f"Valore non valido per {key}") from exc
    for key, value in parsed.items():
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=str(value)))
        else:
            row.value = str(value)
    db.commit()
