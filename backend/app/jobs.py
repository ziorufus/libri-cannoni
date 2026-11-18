"""Job periodici, eseguiti dal worker leader in un task separato dalle fasi.

Ogni job ha un flag di attivazione e un intervallo nelle impostazioni; l'esito
dell'ultima esecuzione è in `job_states`. Due job mettono in pausa automatica
alcune fasi (pause che si sommano a quelle manuali, che restano prioritarie):
- sessione Vinted non valida -> fasi Vinted;
- uso di Claude oltre soglia -> fase di analisi.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .claude_oauth import ClaudeOAuthError, fetch_usage, refresh
from .db import SessionLocal, utcnow
from .listing_sync import apply_listing, item_id
from .models import Book, JobState
from .phases import LISTING_PHASE_NUMBERS
from .vinted_auth import auth_status
from .vinted_listing import PER_PAGE, wardrobe_page
from .vinted_search import VintedOpError

log = logging.getLogger(__name__)

VINTED_PHASES = (2, 3, 4, 5)  # fasi che chiamano Vinted
CLAUDE_PHASES = (1,)  # fasi che usano Claude


@dataclass
class JobResult:
    ok: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Job:
    name: str
    label: str
    description: str
    run: Callable[[dict[str, Any], dict[str, Any]], Awaitable[JobResult]]  # (impostazioni, dati precedenti)
    interval_default: int  # minuti
    retry_key: str | None = None  # intervallo (minuti) dopo un esito negativo, se diverso

    @property
    def enabled_key(self) -> str:
        return f"job_{self.name}_enabled"

    @property
    def interval_key(self) -> str:
        return f"job_{self.name}_interval_minutes"


# --- Sessione Vinted -----------------------------------------------------------

async def check_vinted_session(settings: dict[str, Any], previous: dict[str, Any]) -> JobResult:
    try:
        ok, output = await asyncio.to_thread(auth_status)
    except VintedOpError as exc:
        return JobResult(False, str(exc))
    return JobResult(ok, output)


# --- Uso di Claude --------------------------------------------------------------

def _resets(value: Any) -> str:
    """Data di reset ISO 8601 -> testo locale (es. "18/11 21:39")."""
    if not isinstance(value, str) or not value:
        return ""
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%d/%m %H:%M")
    except ValueError:
        return value


def parse_usage(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Percentuali di sessione (5 ore) e settimana da /api/oauth/usage."""
    s, w = payload.get("five_hour"), payload.get("seven_day")
    if not isinstance(s, dict) or not isinstance(w, dict):
        return None
    try:
        session, week = float(s["utilization"]), float(w["utilization"])
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "session": session,
        "session_resets": _resets(s.get("resets_at")),
        "week": week,
        "week_resets": _resets(w.get("resets_at")),
    }


def _over(value: float, was_over: bool, pause_at: int, resume_below: int) -> bool:
    """Isteresi: si va in pausa alla soglia, si riparte solo sotto il margine."""
    return value >= pause_at or (was_over and value >= resume_below)


async def check_claude_usage(settings: dict[str, Any], previous: dict[str, Any]) -> JobResult:
    # Se la lettura fallisce si mantiene la decisione precedente (pausa o no)
    keep = {k: previous[k] for k in ("session_over", "week_over", "paused", "reason") if k in previous}
    try:
        payload = await fetch_usage()
    except ClaudeOAuthError as exc:
        return JobResult(False, str(exc), keep)
    usage = parse_usage(payload)
    if usage is None:
        raw = json.dumps(payload, ensure_ascii=False)[:2000]
        return JobResult(False, f"Formato della risposta di /api/oauth/usage non riconosciuto:\n{raw}", keep)

    session_over = _over(
        usage["session"], previous.get("session_over", False),
        settings["claude_pause_session_percent"], settings["claude_resume_session_percent"],
    )
    week_over = _over(
        usage["week"], previous.get("week_over", False),
        settings["claude_pause_week_percent"], settings["claude_resume_week_percent"],
    )
    reasons = []
    if session_over:
        reasons.append(f"sessione al {usage['session']:g}%")
    if week_over:
        reasons.append(f"settimana al {usage['week']:g}%")
    data = {
        **usage,
        "session_over": session_over,
        "week_over": week_over,
        "paused": session_over or week_over,
        "reason": "Uso di Claude: " + ", ".join(reasons) if reasons else "",
    }
    msg = f"Sessione {usage['session']:g}% · settimana {usage['week']:g}%"
    if data["paused"]:
        msg += " — analisi in pausa"
    return JobResult(True, msg, data)


# --- Token Claude --------------------------------------------------------------

async def refresh_claude_token(settings: dict[str, Any], previous: dict[str, Any]) -> JobResult:
    try:
        return JobResult(True, await refresh())
    except ClaudeOAuthError as exc:
        return JobResult(False, str(exc))


# --- Guardaroba Vinted ------------------------------------------------------------

def _sync_page(listings: list[dict[str, Any]]) -> tuple[int, list[str]]:
    """Applica una pagina del guardaroba ai libri pubblicati (fasi 6-9)."""
    by_id = {li["id"]: li for li in listings if li.get("id")}
    updated, notes = 0, []
    with SessionLocal() as db:
        books = (
            db.query(Book)
            .filter(
                Book.phase.in_(LISTING_PHASE_NUMBERS),
                Book.vinted_draft.is_not(None),
                Book.sold_manual.is_not(True),  # venduti segnati a mano: non si sincronizzano
            )
            .all()
        )
        for book in books:
            listing = by_id.get(item_id(book))
            if listing is None:
                continue
            if changes := apply_listing(db, book, listing, "guardaroba Vinted (job)"):
                updated += 1
                notes.append(f"#{book.id}: " + ", ".join(changes))
        db.commit()
    return updated, notes


async def sync_wardrobe(settings: dict[str, Any], previous: dict[str, Any]) -> JobResult:
    # Una pagina per giro; dopo l'ultima (meno di PER_PAGE annunci) si ricomincia dalla prima
    page = max(1, int(previous.get("next_page") or 1))
    keep = {"next_page": page}
    try:
        listings = await asyncio.to_thread(wardrobe_page, page)
    except VintedOpError as exc:
        return JobResult(False, str(exc), keep)
    updated, notes = await asyncio.to_thread(_sync_page, listings)
    last = len(listings) < PER_PAGE
    data = {"next_page": 1 if last else page + 1, "page": page, "items": len(listings), "updated": updated}
    msg = f"Pagina {page}{' (ultima)' if last else ''}: {len(listings)} annunci, {updated} libri aggiornati"
    if notes:
        msg += "\n" + "\n".join(notes)
    return JobResult(True, msg, data)


# --- Registro -----------------------------------------------------------------

JOBS: dict[str, Job] = {
    j.name: j
    for j in [
        Job(
            "vinted_session",
            "Sessione Vinted",
            "Verifica i token (come `vinted auth status`) e li rinnova. Se la sessione non è valida "
            "mette in pausa le fasi Vinted, che ripartono da sole quando torna valida.",
            check_vinted_session,
            interval_default=50,
            retry_key="job_vinted_session_retry_minutes",
        ),
        Job(
            "claude_usage",
            "Uso di Claude",
            "Legge l'uso dell'abbonamento (api.anthropic.com/api/oauth/usage, con il token di "
            "CLAUDE_JSON_FILE). Oltre le soglie mette in pausa "
            "l'analisi di Claude, che riparte quando l'uso scende sotto i margini.",
            check_claude_usage,
            interval_default=5,
        ),
        Job(
            "claude_token",
            "Token Claude",
            "Rinnova il token OAuth dell'abbonamento in CLAUDE_JSON_FILE (usato per leggere "
            "l'uso di Claude) e riscrive il file con i nuovi token.",
            refresh_claude_token,
            interval_default=360,
        ),
        Job(
            "vinted_wardrobe",
            "Guardaroba Vinted",
            f"Legge una pagina del guardaroba ({PER_PAGE} annunci) a ogni giro, ricominciando dalla "
            "prima dopo l'ultima, e aggiorna stato (pubblicato, nascosto, riservato, venduto) e "
            "prezzo dei libri pubblicati.",
            sync_wardrobe,
            interval_default=15,
        ),
    ]
}


def load_data(state: JobState | None) -> dict[str, Any]:
    if state is None or not state.data:
        return {}
    try:
        data = json.loads(state.data)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def save_result(db, name: str, result: JobResult, when: datetime | None = None) -> None:
    state = db.get(JobState, name) or JobState(name=name)
    state.last_run_at = when or utcnow()
    state.ok = result.ok
    state.message = result.message[:4000]
    state.data = json.dumps(result.data, ensure_ascii=False) if result.data else None
    db.merge(state)


def next_run(job: Job, state: JobState | None, settings: dict[str, Any]) -> datetime | None:
    """Prossima esecuzione; None se il job è disattivato."""
    if not settings.get(job.enabled_key):
        return None
    if state is None or state.last_run_at is None:
        return utcnow()
    minutes = settings[job.interval_key]
    if state.ok is False and job.retry_key:
        minutes = min(minutes, settings[job.retry_key])
    return state.last_run_at + timedelta(minutes=minutes)


def auto_pauses(db, settings: dict[str, Any]) -> dict[int, str]:
    """Fasi in pausa automatica, con il motivo. Un job disattivato non mette pause."""
    states = {s.name: s for s in db.query(JobState)}
    pauses: dict[int, str] = {}
    vinted = states.get("vinted_session")
    if settings.get(JOBS["vinted_session"].enabled_key) and vinted is not None and vinted.ok is False:
        for phase in VINTED_PHASES:
            pauses[phase] = "Sessione Vinted non valida: reimporta i token da Admin → Vinted."
    claude = states.get("claude_usage")
    data = load_data(claude)
    if settings.get(JOBS["claude_usage"].enabled_key) and data.get("paused"):
        for phase in CLAUDE_PHASES:
            pauses[phase] = f"{data.get('reason') or 'Uso di Claude oltre soglia'} (riparte sotto i margini impostati)."
    return pauses
