"""Fasi di lavorazione di un libro.

Ogni fase ha un "handler" eseguito dal processo in background. Se l'handler
ha successo il libro passa alla fase successiva; altrimenti si ritenta secondo
le impostazioni (numero di tentativi, attesa). Una fase senza handler è uno
stato di attesa: il libro resta lì finché non si aggiungono nuove fasi.

Prima di eseguire una fase il processo controlla:
- i requisiti della fase (`requires`): se uno non è soddisfatto il libro resta
  "bloccato" con il motivo, finché non si correggono i dati (non si forza);
- la pausa della fase (impostazione "in pausa"): il libro resta "in pausa"
  finché la pausa non viene tolta o un admin non forza quel singolo libro.

Per aggiungere una fase basta scrivere un handler e registrarlo in PHASES.
"""

import asyncio
import json
import logging
import math
import os
import shlex
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any

from .claude_json import book_condition, book_isbn, book_name, claude_number, claude_value, parse_output
from .config import config
from .storage import book_dir
from .vinted_draft import NO_ISBN, DraftData, create_draft, item_state, publish_draft, render_description
from .vinted_search import VintedOpError, describe, price_summary, search_prices

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
    claude_output: str | None = None
    description: str | None = None
    price: float | None = None
    condition: str | None = None
    condition_manual: bool = False
    vinted_draft: str | None = None


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
    # False = errore nei dati: inutile ritentare, il libro va subito in "fallito"
    retry: bool = True
    # Solo per le fasi che controllano più volte (es. approvazione Vinted):
    retry_delay_minutes: float | None = None  # attesa tra i tentativi, invece di quella globale
    max_attempts: int | None = None  # tentativi massimi, invece di quelli globali
    # Esito positivo: attesa prima di eseguire la fase successiva
    next_phase_delay_minutes: float | None = None
    # Esito negativo: riporta il libro a questa fase, fermo (gli `updates` si applicano)
    goto_phase: int | None = None


Handler = Callable[[BookSnapshot], Awaitable[PhaseResult]]
# Requisito: riceve il libro (riga del DB) e le impostazioni; restituisce il
# motivo del blocco, oppure None se è soddisfatto.
Requirement = Callable[[Any, dict[str, Any]], str | None]


@dataclass(frozen=True)
class Phase:
    number: int
    label: str
    description: str
    handler: Handler | None = None
    # Attesa minima predefinita (secondi) tra la fine di un'esecuzione di questa
    # fase e l'inizio della successiva; modificabile nelle Impostazioni.
    min_gap_default: int = 0
    requires: tuple[Requirement, ...] = ()
    # Valore iniziale dell'impostazione "in pausa"
    paused_default: bool = False


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


async def call_claude(book: BookSnapshot, prompt: str) -> PhaseResult:
    """Esegue `claude -p` nella cartella del libro con gli argomenti della fase 1.

    Esito positivo: `output` è la risposta (non vuota). Esito negativo: `error`
    spiega il problema (si ritenta)."""
    if not config.claude_code_oauth_token:
        return PhaseResult(False, error="CLAUDE_CODE_OAUTH_TOKEN non impostato nel backend")

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
    return PhaseResult(True, output=out)


async def run_claude(book: BookSnapshot) -> PhaseResult:
    answer = await call_claude(book, build_prompt(book.settings["claude_prompt"], book))
    if not answer.ok:
        return answer
    out = answer.output
    updates = {"claude_output": out}
    # Se l'output è un JSON con il titolo, il libro prende il nome "Titolo - Autore".
    # Se non lo è, la fase riesce comunque e il nome resta vuoto.
    name = book_name(
        out, book.settings["json_title_key"], book.settings["json_author_key"], book.settings["title_max_length"]
    )
    if name:
        updates["description"] = name
    fill = {}
    if condition := book_condition(out, book.settings["json_condition_key"]):
        fill["condition"] = condition
    return PhaseResult(True, output=out, updates=updates, fill_if_empty=fill)


# --- Fase 2: prezzi degli stessi libri su Vinted -------------------------------

async def search_vinted(book: BookSnapshot) -> PhaseResult:
    isbn = book_isbn(book.claude_output, book.settings["json_isbn_key"])
    if not isbn:
        # Niente ISBN: la fase si considera fatta, senza chiamare Vinted; il
        # prezzo (se manca) è quello proposto da Claude
        claude_price = claude_number(book.claude_output, book.settings["json_price_key"])
        return PhaseResult(
            True,
            output="ISBN non presente nel JSON di Claude: ricerca su Vinted saltata",
            updates={"vinted_prices": None},
            fill_if_empty=_price_fill(claude_price, book.settings),
        )
    try:
        result = await asyncio.to_thread(
            search_prices,
            isbn,
            book.settings["vinted_search_categories"],
            book.settings["vinted_search_conditions"],
            book.settings["vinted_search_limit"],
        )
    except VintedOpError as exc:
        return PhaseResult(False, error=str(exc), retry=not exc.permanent)
    output = describe(result)
    if result["items"] and book.settings["vinted_check_enabled"]:
        # Claude dice quali annunci riguardano davvero il libro: la mediana si
        # calcola solo su quelli (se nessuno lo è, il prezzo resta vuoto)
        check = await check_listings(book, isbn, result)
        if not check.ok:
            return check
        output += "\n" + check.output
    # Anche senza risultati la fase è fatta: si salva la ricerca con elenco vuoto.
    # Il prezzo (se manca) è la mediana di Vinted; senza annunci resta vuoto.
    median = (result["summary"] or {}).get("median")
    return PhaseResult(
        True,
        output=output,
        updates={"vinted_prices": json.dumps(result, ensure_ascii=False)},
        fill_if_empty=_price_fill(median, book.settings),
    )


def _cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "/").replace("\n", " ").strip()


def listings_table(items: list[dict[str, Any]]) -> str:
    """Tabella markdown degli annunci (segnaposto {elenco}), numerati da 1."""
    rows = ["| n. | Titolo dell'annuncio | Prezzo | Condizioni |", "| --- | --- | --- | --- |"]
    for n, it in enumerate(items, 1):
        price = "" if it.get("price") is None else f"{it['price']:g} {it.get('currency') or ''}"
        rows.append(f"| {n} | {_cell(it.get('title'))} | {_cell(price)} | {_cell(it.get('condition'))} |")
    return "\n".join(rows)


def _compatible_numbers(answer: str, count: int) -> tuple[list[int], str] | None:
    """Numeri degli annunci compatibili (1..count, senza ripetizioni) e motivo,
    dal JSON di risposta; None se la risposta non è leggibile."""
    data = parse_output(answer)
    if data is None or not isinstance(data.get("compatibili"), list):
        return None
    numbers: list[int] = []
    for v in data["compatibili"]:
        try:
            n = int(str(v).strip())
        except ValueError:
            continue
        if 1 <= n <= count and n not in numbers:
            numbers.append(n)
    reason = data.get("motivo")
    return sorted(numbers), str(reason).strip() if reason not in (None, "") else ""


async def check_listings(book: BookSnapshot, isbn: str, result: dict[str, Any]) -> PhaseResult:
    """Chiede a Claude quali annunci di `result` riguardano il libro e aggiorna
    `result` (in place): `compatible` su ogni annuncio, `summary` sui soli
    compatibili, `summary_all` su tutti, `check` con l'esito."""
    s = book.settings
    items = result["items"]
    title_author = (
        book_name(book.claude_output, s["json_title_key"], s["json_author_key"])
        or (book.description or "").strip()
        or "(titolo non noto)"
    )
    prompt = (
        s["vinted_check_prompt"]
        .replace("{titoloautore}", title_author)
        .replace("{isbn}", isbn)
        .replace("{elenco}", listings_table(items))
    )
    answer = await call_claude(book, prompt)
    if not answer.ok:
        return PhaseResult(False, output=answer.output, error=f"Controllo degli annunci con Claude: {answer.error}")
    parsed = _compatible_numbers(answer.output, len(items))
    if parsed is None:
        return PhaseResult(
            False,
            output=answer.output,
            error='Controllo degli annunci: la risposta di Claude non è un JSON con la chiave "compatibili"',
        )
    numbers, reason = parsed
    for n, it in enumerate(items, 1):
        it["compatible"] = n in numbers
    compatible = [it for it in items if it["compatible"]]
    result["summary_all"] = result["summary"]
    result["summary"] = price_summary(compatible)
    result["check"] = {"compatible": numbers, "reason": reason, "answer": answer.output}
    msg = f"Claude: {len(compatible)} annunci compatibili su {len(items)}"
    if compatible and result["summary"]:
        msg += f" (mediana {result['summary']['median']:g})"
    if reason:
        msg += f" — {reason}"
    return PhaseResult(True, output=msg)


def no_compatible_listings(book) -> str | None:
    """Motivo dato da Claude se ha scartato tutti gli annunci trovati; None altrimenti."""
    try:
        prices = json.loads(book.vinted_prices) if book.vinted_prices else None
    except ValueError:
        return None
    check = (prices or {}).get("check") if isinstance(prices, dict) else None
    if not isinstance(check, dict) or check.get("compatible"):
        return None
    return check.get("reason") or ""


def auto_price(value: float | None, minimum: int) -> int | None:
    """Arrotonda all'euro (0,50 per eccesso) e applica il minimo."""
    if value is None:
        return None
    return max(int(math.floor(value + 0.5)), minimum)


def _price_fill(value: float | None, settings: dict[str, Any]) -> dict[str, Any]:
    price = auto_price(value, settings["price_min_euro"])
    return {} if price is None else {"price": price}


# --- Fase 3: bozza su Vinted -------------------------------------------------

def suggested_price(book, settings: dict[str, Any]) -> int | None:
    """Prezzo secondo le regole della fase 2 (mediana di Vinted con ISBN, prezzo di
    Claude senza), arrotondato e con il minimo; None se non è calcolabile."""
    if book_isbn(book.claude_output, settings["json_isbn_key"]):
        try:
            prices = json.loads(book.vinted_prices) if book.vinted_prices else {}
        except ValueError:
            prices = {}
        value = ((prices or {}).get("summary") or {}).get("median")
    else:
        value = claude_number(book.claude_output, settings["json_price_key"])
    return auto_price(value, settings["price_min_euro"])


def require_price(book, settings: dict[str, Any]) -> str | None:
    if book.price is not None:
        return None
    if (suggested := suggested_price(book, settings)) is not None:
        # Es. libri passati dalla fase 2 prima che esistesse il calcolo automatico
        return (
            f"Manca il prezzo: quello calcolato ({suggested} €) non è stato applicato. "
            "Usa \"Usa il prezzo calcolato\" nella sezione Prezzo, o inseriscilo a mano."
        )
    if book_isbn(book.claude_output, settings["json_isbn_key"]):
        if (reason := no_compatible_listings(book)) is not None:
            why = f" ({reason})" if reason else ""
            return (
                f"Manca il prezzo: nessuno degli annunci trovati su Vinted riguarda questo libro{why}. "
                "Inserisci il prezzo a mano."
            )
        return "Manca il prezzo: ISBN presente ma nessun prezzo trovato su Vinted. Inserisci il prezzo a mano."
    return "Manca il prezzo: nessun ISBN e nessun prezzo da Claude. Inserisci il prezzo a mano."


def _claude(book: BookSnapshot, key_setting: str) -> str:
    return claude_value(book.claude_output, book.settings[key_setting])


async def create_vinted_draft(book: BookSnapshot) -> PhaseResult:
    if book.vinted_draft:
        # Mai due bozze per lo stesso libro (es. dopo "Sposta a fase")
        draft = json.loads(book.vinted_draft)
        return PhaseResult(True, output=f"Bozza già creata in precedenza: {draft.get('edit_url')}")

    def fail(msg: str) -> PhaseResult:
        return PhaseResult(False, error=msg, retry=False)

    s = book.settings
    title = (book.description or "").strip()
    if not title:
        return fail("Il libro non ha un nome (titolo dell'annuncio)")
    if len(title) > (limit := s["title_max_length"]):
        return fail(f"Il nome del libro è lungo {len(title)} caratteri: Vinted ne accetta al massimo {limit}. Accorcialo.")
    if book.price is None:
        return fail("Manca il prezzo")

    category = _claude(book, "json_category_key")
    if not category:
        return fail(f'Il JSON di Claude non contiene la categoria (chiave "{s["json_category_key"]}")')
    if book.condition_manual and book.condition:
        condition = book.condition
    else:
        condition = _claude(book, "json_vinted_condition_key")
        if not condition:
            return fail(
                f'Il JSON di Claude non contiene le condizioni per Vinted (chiave "{s["json_vinted_condition_key"]}")'
            )
    language = _claude(book, "json_language_key")
    if not language:
        return fail(f'Il JSON di Claude non contiene la lingua (chiave "{s["json_language_key"]}")')
    language = language[:1].upper() + language[1:]

    keys = {
        "titolo": "json_title_key",
        "autore": "json_author_key",
        "editore": "json_publisher_key",
        "anno": "json_year_key",
        "collana": "json_series_key",
        "lingua": "json_language_key",
        "categoria": "json_category_key",
        "descrizione": "json_description_key",
    }
    values = {name: _claude(book, key) for name, key in keys.items()}
    # testo descrittivo dal JSON; in mancanza, le condizioni del libro (ottime/buone/discrete)
    values["condizioni"] = _claude(book, "json_condition_text_key") or book.condition or ""

    data = DraftData(
        title=title,
        description=render_description(s["vinted_description_template"], values),
        isbn=book_isbn(book.claude_output, s["json_isbn_key"]) or NO_ISBN,
        category=category,
        condition=condition,
        language=language,
        price=book.price,
        photos=[book_dir(book.id) / f for f in book.photo_files],
    )
    try:
        draft = await asyncio.to_thread(create_draft, data)
    except VintedOpError as exc:
        return PhaseResult(False, error=str(exc), retry=not exc.permanent)
    return PhaseResult(
        True,
        output=f"Bozza {draft['draft_id']} creata: {draft['edit_url']}",
        updates={"vinted_draft": json.dumps(draft, ensure_ascii=False)},
    )


# --- Fase 4: pubblicazione; fase 5: approvazione di Vinted --------------------

PUBLISH_PHASE = 4


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _draft(book: BookSnapshot) -> dict[str, Any] | None:
    try:
        draft = json.loads(book.vinted_draft) if book.vinted_draft else None
    except ValueError:
        return None
    return draft if isinstance(draft, dict) and draft.get("draft_id") else None


async def publish_vinted(book: BookSnapshot) -> PhaseResult:
    draft = _draft(book)
    if draft is None:
        return PhaseResult(False, error="Il libro non ha una bozza su Vinted (rifai la fase 3)", retry=False)
    try:
        url = await asyncio.to_thread(publish_draft, draft["draft_id"])
    except VintedOpError as exc:
        return PhaseResult(False, error=str(exc), retry=not exc.permanent)
    draft.update(publish_requested_at=_now_iso(), item_url=url, published_at=None)
    wait = book.settings["publish_check_interval_minutes"]
    return PhaseResult(
        True,
        output=f"Pubblicazione della bozza {draft['draft_id']} richiesta: in attesa dell'approvazione di Vinted",
        updates={"vinted_draft": json.dumps(draft, ensure_ascii=False)},
        # Primo controllo dopo l'intervallo: subito dopo la richiesta Vinted
        # potrebbe ancora mostrare la bozza
        next_phase_delay_minutes=wait,
    )


async def check_publication(book: BookSnapshot) -> PhaseResult:
    draft = _draft(book)
    if draft is None:
        return PhaseResult(False, error="Il libro non ha una bozza su Vinted", retry=False)
    polling = {
        "retry_delay_minutes": book.settings["publish_check_interval_minutes"],
        "max_attempts": book.settings["publish_check_max_attempts"],
    }
    try:
        state = await asyncio.to_thread(item_state, draft["draft_id"])
    except VintedOpError as exc:
        return PhaseResult(False, error=str(exc), **polling)
    if state == "review":
        return PhaseResult(False, error="Ancora in approvazione su Vinted (l'annuncio risponde 404)", **polling)
    if state == "draft":
        draft.update(publish_requested_at=None, returned_to_draft_at=_now_iso())
        return PhaseResult(
            False,
            error="Vinted non ha approvato l'annuncio e l'ha riportato in bozza. Controllalo sul sito, "
            "poi rilancia la pubblicazione con \"Esegui la fase\".",
            updates={"vinted_draft": json.dumps(draft, ensure_ascii=False)},
            goto_phase=PUBLISH_PHASE,
        )
    draft.update(published_at=_now_iso())
    return PhaseResult(
        True,
        output=f"Annuncio pubblicato: {draft.get('item_url')}",
        updates={"vinted_draft": json.dumps(draft, ensure_ascii=False)},
    )


# --- Registro delle fasi -----------------------------------------------------

PHASES: dict[int, Phase] = {
    p.number: p
    for p in [
        Phase(0, "Inserito", "Libro inserito: controllo delle fotografie.", check_photos),
        Phase(1, "Analisi Claude", "Invio di foto, prompt e note a `claude -p`.", run_claude, min_gap_default=30),
        Phase(
            2,
            "Prezzi Vinted",
            "Ricerca su Vinted, per ISBN, degli annunci dello stesso libro e dei loro prezzi.",
            search_vinted,
            min_gap_default=30,
        ),
        Phase(
            3,
            "Bozza Vinted",
            "Creazione della bozza di annuncio su Vinted (foto, titolo, descrizione, prezzo, ...).",
            create_vinted_draft,
            min_gap_default=30,
            requires=(require_price,),
            paused_default=True,
        ),
        Phase(
            PUBLISH_PHASE,
            "Pubblicazione",
            "Richiesta a Vinted di pubblicare la bozza (come `vinted publish`).",
            publish_vinted,
            min_gap_default=30,
            paused_default=True,
        ),
        Phase(
            5,
            "Approvazione Vinted",
            "Controllo periodico (come `vinted item`) finché Vinted approva l'annuncio "
            "o lo riporta in bozza (in quel caso il libro torna fermo alla fase 4).",
            check_publication,
            min_gap_default=5,
        ),
        Phase(6, "Pubblicato", "Annuncio pubblicato e visibile su Vinted."),
        Phase(7, "Nascosto", "Annuncio nascosto su Vinted (\"Mostra di nuovo\" lo rende visibile)."),
        Phase(8, "Riservato", "Annuncio riservato su Vinted: venduto, in attesa di conclusione."),
        Phase(9, "Venduto", "Libro venduto: su Vinted, in attesa che l'acquirente confermi la ricezione."),
        Phase(
            10,
            "Pagato",
            "Incasso accreditato: l'acquirente ha confermato la ricezione su Vinted "
            "(o, per i venduti segnati a mano, \"Segna come pagato\").",
        ),
        Phase(11, "Versato", "Ricavato versato a Emergency (vedi Versamenti)."),
    ]
}

ACTIVE_PHASES = [n for n, p in PHASES.items() if p.handler is not None]

# Fasi di un annuncio uscito dall'approvazione: si cambiano solo con le azioni
# su Vinted (nascondi/mostra, venduto) o con "Aggiorna" (lettura del guardaroba)
LISTING_PHASES = {"live": 6, "hidden": 7, "reserved": 8, "sold": 9}
LISTING_PHASE_NUMBERS = frozenset(LISTING_PHASES.values())

# Pagato: Vinted ha accreditato l'incasso (acquirente che conferma la ricezione,
# letto dagli ordini) o "Segna come pagato" per i venduti segnati a mano.
# Versato: si entra e si esce solo dalla pagina Versamenti.
SOLD_PHASE = LISTING_PHASES["sold"]
PAID_PHASE = 10
DONATED_PHASE = 11
# Libri ceduti, il cui ricavato va (o è andato) a Emergency
SOLD_PHASES = (SOLD_PHASE, PAID_PHASE, DONATED_PHASE)
# Si possono includere in un nuovo versamento
DONATABLE_PHASES = (SOLD_PHASE, PAID_PHASE)
MANUAL_ONLY_PHASES = LISTING_PHASE_NUMBERS | {PAID_PHASE, DONATED_PHASE}


def min_gap_key(phase: int) -> str:
    return f"phase_{phase}_min_gap_seconds"


def pause_key(phase: int) -> str:
    return f"phase_{phase}_paused"


def unmet_requirements(book, settings: dict[str, Any]) -> list[str]:
    phase = PHASES.get(book.phase)
    if phase is None:
        return []
    return [msg for req in phase.requires if (msg := req(book, settings))]


def initial_status(phase: int) -> str:
    p = PHASES.get(phase)
    return "ready" if p is not None and p.handler is not None else "idle"
