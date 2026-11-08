from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..security import CurrentUser, require_admin
from ..settings_store import all_settings, update_settings

router = APIRouter(prefix="/api/admin/settings", tags=["settings"])


class SettingsIn(BaseModel):
    values: dict[str, Any]


@router.get("")
def read_settings(db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    return all_settings(db)


@router.put("")
def write_settings(
    body: SettingsIn,
    request: Request,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require_admin),
):
    try:
        update_settings(db, body.values)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    request.app.state.worker.wake()  # applica subito il nuovo intervallo
    return all_settings(db)
