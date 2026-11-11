from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..jobs import JOBS, load_data, next_run
from ..models import JobState
from ..phases import PHASES
from ..security import CurrentUser, require_admin
from ..worker import load_settings

router = APIRouter(prefix="/api/admin/jobs", tags=["jobs"])


def jobs_overview(db: Session) -> dict:
    settings = load_settings(db)
    jobs = []
    for job in JOBS.values():
        state = db.get(JobState, job.name)
        due = next_run(job, state, settings)
        jobs.append(
            {
                "name": job.name,
                "label": job.label,
                "description": job.description,
                "enabled": bool(settings[job.enabled_key]),
                "interval_minutes": settings[job.interval_key],
                "last_run_at": state.last_run_at.isoformat() if state and state.last_run_at else None,
                "ok": state.ok if state else None,
                "message": state.message if state else "",
                "data": load_data(state),
                "next_run_at": due.isoformat() if due else None,
            }
        )
    pauses = [
        {"phase": n, "label": PHASES[n].label, "reason": reason}
        for n, reason in sorted(settings["auto_pauses"].items())
    ]
    return {"jobs": jobs, "auto_pauses": pauses}


@router.get("")
def list_jobs(db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    return jobs_overview(db)


@router.post("/{name}/run")
def run_job(name: str, request: Request, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    """Esegue il job al prossimo giro del worker (subito, se il job è attivo)."""
    if name not in JOBS:
        raise HTTPException(404, "Job inesistente")
    state = db.get(JobState, name) or JobState(name=name)
    state.last_run_at = None
    db.merge(state)
    db.commit()
    request.app.state.worker.wake_jobs()
    return jobs_overview(db)
