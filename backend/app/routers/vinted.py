import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..security import CurrentUser, require_admin
from ..vinted_auth import auth_import, auth_status
from ..vinted_search import VintedOpError

router = APIRouter(prefix="/api/admin/vinted", tags=["vinted"])


class CurlIn(BaseModel):
    curl: str = Field(min_length=1, max_length=200_000)


async def _call(fn, *args) -> dict:
    try:
        ok, output = await asyncio.to_thread(fn, *args)
    except VintedOpError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"ok": ok, "output": output}


@router.get("/status")
async def vinted_status(_: CurrentUser = Depends(require_admin)):
    """Come `vinted auth status`: file dei token e utente collegato."""
    return await _call(auth_status)


@router.post("/auth-import")
async def vinted_auth_import(body: CurlIn, _: CurrentUser = Depends(require_admin)):
    """Come `vinted auth import`: legge i token da un "Copy as cURL" e li salva."""
    if "curl" not in body.curl[:200].lower():
        raise HTTPException(400, "Incolla una richiesta copiata con \"Copy as cURL\" (deve iniziare con curl)")
    return await _call(auth_import, body.curl)
