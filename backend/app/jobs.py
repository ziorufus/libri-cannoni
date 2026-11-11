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
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .config import config
from .db import utcnow
from .models import JobState
from .vinted_auth import auth_status
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

_SESSION = re.compile(r"Current session:\s*(\d+(?:\.\d+)?)%\s*used(?:\s*·\s*resets\s+(.+))?", re.I)
_WEEK = re.compile(r"Current week \(all models\):\s*(\d+(?:\.\d+)?)%\s*used(?:\s*·\s*resets\s+(.+))?", re.I)


def parse_usage(text: str) -> dict[str, Any] | None:
    s, w = _SESSION.search(text), _WEEK.search(text)
    if not s or not w:
        return None
    return {
        "session": float(s[1]),
        "session_resets": (s[2] or "").strip(),
        "week": float(w[1]),
        "week_resets": (w[2] or "").strip(),
    }


def _over(value: float, was_over: bool, pause_at: int, resume_below: int) -> bool:
    """Isteresi: si va in pausa alla soglia, si riparte solo sotto il margine."""
    return value >= pause_at or (was_over and value >= resume_below)


async def check_claude_usage(settings: dict[str, Any], previous: dict[str, Any]) -> JobResult:
    # Se la lettura fallisce si mantiene la decisione precedente (pausa o no)
    keep = {k: previous[k] for k in ("session_over", "week_over", "paused", "reason") if k in previous}
    if not config.claude_code_oauth_token:
        return JobResult(False, "CLAUDE_CODE_OAUTH_TOKEN non impostato nel backend", keep)
    env = {**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": config.claude_code_oauth_token}
    try:
        proc = await asyncio.create_subprocess_exec(
            config.claude_bin, "-p", "/usage",
            cwd=config.books_dir,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return JobResult(False, f"Impossibile avviare {config.claude_bin}: {exc}", keep)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), 120)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return JobResult(False, "claude -p /usage: nessuna risposta dopo 120 secondi", keep)
    text = out.decode(errors="replace")
    usage = parse_usage(text)
    if usage is None:
        raw = (text + "\n" + err.decode(errors="replace")).strip()[:2000]
        return JobResult(False, f"Formato di /usage non riconosciuto:\n{raw}", keep)

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
            "Legge l'uso dell'abbonamento (claude -p /usage). Oltre le soglie mette in pausa "
            "l'analisi di Claude, che riparte quando l'uso scende sotto i margini.",
            check_claude_usage,
            interval_default=5,
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
