"""Whitelist (e-mail e domini) e utenti: solo amministratori."""

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..access import normalize_domain, normalize_email, resolve_role
from ..config import config
from ..db import get_db
from ..models import AccessEntry, User
from ..security import CurrentUser, require_admin

router = APIRouter(prefix="/api/admin", tags=["access"])

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DOMAIN_RE = re.compile(r"^([a-z0-9-]+\.)+[a-z]{2,}$")


class EntryIn(BaseModel):
    kind: Literal["email", "domain"]
    value: str
    role: Literal["user", "admin"] = "user"
    note: str = ""


class EntryPatch(BaseModel):
    role: Literal["user", "admin"] | None = None
    note: str | None = None


def _out(e: AccessEntry) -> dict:
    return {
        "id": e.id,
        "kind": e.kind,
        "value": e.value,
        "role": e.role,
        "note": e.note,
        "created_at": e.created_at,
        "protected": e.kind == "email" and e.value == normalize_email(config.admin_email),
    }


def _check_not_locked_out(db: Session, cu: CurrentUser) -> None:
    db.flush()
    if resolve_role(db, cu.email) != "admin":
        db.rollback()
        raise HTTPException(400, "Non puoi togliere a te stesso i permessi di amministratore")


def _get(db: Session, entry_id: int) -> AccessEntry:
    entry = db.get(AccessEntry, entry_id)
    if entry is None:
        raise HTTPException(404, "Voce non trovata")
    if _out(entry)["protected"]:
        raise HTTPException(400, "L'amministratore definito da ADMIN_EMAIL non è modificabile")
    return entry


@router.get("/access")
def list_entries(db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    entries = db.query(AccessEntry).order_by(AccessEntry.kind, AccessEntry.value).all()
    return [_out(e) for e in entries]


@router.post("/access", status_code=201)
def create_entry(body: EntryIn, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    if body.kind == "email":
        value = normalize_email(body.value)
        if not EMAIL_RE.match(value):
            raise HTTPException(400, "Indirizzo e-mail non valido")
    else:
        value = normalize_domain(body.value)
        if not DOMAIN_RE.match(value):
            raise HTTPException(400, "Dominio non valido (esempio: emergency.it)")
    entry = AccessEntry(kind=body.kind, value=value, role=body.role, note=body.note.strip())
    db.add(entry)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Voce già presente")
    return _out(entry)


@router.patch("/access/{entry_id}")
def update_entry(
    entry_id: int,
    body: EntryPatch,
    db: Session = Depends(get_db),
    cu: CurrentUser = Depends(require_admin),
):
    entry = _get(db, entry_id)
    if body.role is not None:
        entry.role = body.role
    if body.note is not None:
        entry.note = body.note.strip()
    _check_not_locked_out(db, cu)
    db.commit()
    return _out(entry)


@router.delete("/access/{entry_id}", status_code=204)
def delete_entry(entry_id: int, db: Session = Depends(get_db), cu: CurrentUser = Depends(require_admin)):
    db.delete(_get(db, entry_id))
    _check_not_locked_out(db, cu)
    db.commit()


@router.get("/users")
def list_users(db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    users = db.query(User).order_by(User.last_login_at.desc()).all()
    return [
        {
            "id": u.id,
            "email": u.email,
            "name": u.name,
            "last_provider": u.last_provider,
            "created_at": u.created_at,
            "last_login_at": u.last_login_at,
            "role": resolve_role(db, u.email),
        }
        for u in users
    ]
