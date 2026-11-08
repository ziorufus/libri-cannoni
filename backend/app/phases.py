"""Fasi di lavorazione di un libro.

Ogni fase ha un "handler" eseguito dal processo in background. Se l'handler
ha successo il libro passa alla fase successiva; altrimenti si ritenta secondo
le impostazioni (numero di tentativi, attesa). Una fase senza handler è uno
stato di attesa: il libro resta lì finché non si aggiungono nuove fasi.

Per aggiungere una fase basta scrivere un handler e registrarlo in PHASES.
"""

import asyncio
import logging
import os
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .claude_json import book_condition, book_name
from .config import config
from .storage import book_dir

log = logging.getLogger(__name__)


@dataclass
class BookSnapshot:
    """Dati del libro letti dal DB prima di eseguire una fase."""

    id: int
    phase: int
    attempt: int
    notes: str
    photo_files: list[str]
    settings: dict[str, Any]


@dataclass
class PhaseResult:
    ok: bool
    output: str = ""
    error: str = ""
    # Colonne del libro da aggiornare in caso di successo
    updates: dict[str, Any] = field(default_factory=dict)
    # Colonne da riempire in caso di successo SOLO se sono vuote nel DB
    # (es. condizioni: un valore scelto a mano non viene mai sovrascritto)
    fill_if_empty: dict[str, Any] = field(default_factory=dict)


Handler = Callable[[BookSnapshot], Awaitable[PhaseResult]]


@dataclass(frozen=True)
class Phase:
    number: int
    label: str
    description: str
    handler: Handler | None = None
    # Attesa minima predefinita (secondi) tra la fine di un'esecuzione di questa
    # fase e l'inizio della successiva; modificabile nelle Impostazioni.
    min_gap_default: int = 0


# --- Fase 0: controllo dell'inserimento -------------------------------------

async def check_photos(book: BookSnapshot) -> PhaseResult:
    folder = book_dir(book.id)
    missing = [f for f in book.photo_files if not (folder / f).is_file()]
    if not book.photo_files:
        return PhaseResult(False, error="Il libro non ha fotografie")
    if missing:
        return PhaseResult(False, error=f"File mancanti in {folder}: {', '.join(missing)}")
    return PhaseResult(True, output=f"{len(book.photo_files)} fotografie presenti in {folder}")


# --- Fase 1: analisi con Claude -----------------------------------------------

def build_prompt(template: str, book: BookSnapshot) -> str:
    notes = book.notes.strip() or "(nessuna nota)"
    photos = ", ".join(book.photo_files)
    # replace e non str.format: il prompt può contenere altre graffe
    return template.replace("{foto}", photos).replace("{note}", notes)


async def run_claude(book: BookSnapshot) -> PhaseResult:
    if not config.claude_code_oauth_token:
        return PhaseResult(False, error="CLAUDE_CODE_OAUTH_TOKEN non impostato nel backend")

    prompt = build_prompt(book.settings["claude_prompt"], book)
    args = [config.claude_bin, "-p", *shlex.split(book.settings["claude_extra_args"])]
    env = {**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": config.claude_code_oauth_token}
    timeout = book.settings["claude_timeout_seconds"]

    log.info("Libro %s: avvio %s", book.id, shlex.join(args))
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=book_dir(book.id),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return PhaseResult(False, error=f"Impossibile avviare {config.claude_bin}: {exc}")

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return PhaseResult(False, error=f"Timeout dopo {timeout} secondi")
    except asyncio.CancelledError:
        # Spegnimento del server: non lasciamo processi orfani
        proc.kill()
        raise

    out = stdout.decode(errors="replace").strip()
    err = stderr.decode(errors="replace").strip()
    if proc.returncode != 0:
        return PhaseResult(False, output=out, error=f"Uscita con codice {proc.returncode}: {err or out}"[:4000])
    if not out:
        return PhaseResult(False, error=f"Risposta vuota. {err}"[:4000])
    updates = {"claude_output": out}
    # Se l'output è un JSON con il titolo, il libro prende il nome "Titolo - Autore".
    # Se non lo è, la fase riesce comunque e il nome resta vuoto.
    name = book_name(out, book.settings["json_title_key"], book.settings["json_author_key"])
    if name:
        updates["description"] = name
    fill = {}
    if condition := book_condition(out, book.settings["json_condition_key"]):
        fill["condition"] = condition
    return PhaseResult(True, output=out, updates=updates, fill_if_empty=fill)


# --- Registro delle fasi -----------------------------------------------------

PHASES: dict[int, Phase] = {
    p.number: p
    for p in [
        Phase(0, "Inserito", "Libro inserito: controllo delle fotografie.", check_photos),
        Phase(1, "Analisi Claude", "Invio di foto, prompt e note a `claude -p`.", run_claude, min_gap_default=30),
        Phase(2, "Descritto", "Descrizione ottenuta. In attesa delle fasi successive."),
    ]
}

ACTIVE_PHASES = [n for n, p in PHASES.items() if p.handler is not None]


def min_gap_key(phase: int) -> str:
    return f"phase_{phase}_min_gap_seconds"


def initial_status(phase: int) -> str:
    p = PHASES.get(phase)
    return "ready" if p is not None and p.handler is not None else "idle"
