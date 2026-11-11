import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..db import SessionLocal
from ..jobs import JobResult, save_result
from ..security import CurrentUser, require_admin
from ..worker import apply_pauses
from ..vinted_auth import auth_import, auth_status
from ..vinted_search import VintedOpError

router = APIRouter(prefix="/api/admin/vinted", tags=["vinted"])


class CurlIn(BaseModel):
    curl: str = Field(min_length=1, max_length=200_000)


def _record(ok: bool, output: str) -> None:
    """L'esito vale come un controllo del job "Sessione Vinted": se la sessione
    torna valida le fasi Vinted ripartono subito (e viceversa)."""
    with SessionLocal() as db:
        save_result(db, "vinted_session", JobResult(ok, output))
        db.commit()
        apply_pauses(db)


async def _call(request: Request, fn, *args) -> dict:
    try:
        ok, output = await asyncio.to_thread(fn, *args)
    except VintedOpError as exc:
        raise HTTPException(502, str(exc)) from exc
    await asyncio.to_thread(_record, ok, output)
    request.app.state.worker.wake()
    return {"ok": ok, "output": output}


@router.get("/status")
async def vinted_status(request: Request, _: CurrentUser = Depends(require_admin)):
    """Come `vinted auth status`: file dei token e utente collegato."""
    return await _call(request, auth_status)


@router.post("/auth-import")
async def vinted_auth_import(body: CurlIn, request: Request, _: CurrentUser = Depends(require_admin)):
    """Come `vinted auth import`: legge i token da un "Copy as cURL" e li salva."""
    if "curl" not in body.curl[:200].lower():
        raise HTTPException(400, "Incolla una richiesta copiata con \"Copy as cURL\" (deve iniziare con curl)")
    return await _call(request, auth_import, body.curl)
