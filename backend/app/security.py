"""Token di sessione (JWT) e dipendenze FastAPI per i permessi."""

from dataclasses import dataclass
from datetime import timedelta

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .access import resolve_role
from .config import config
from .db import get_db, utcnow
from .models import User

ALGORITHM = "HS256"
_bearer = HTTPBearer(auto_error=False)


def create_token(user: User) -> str:
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "exp": utcnow() + timedelta(hours=config.jwt_expire_hours),
    }
    return jwt.encode(payload, config.secret_key, algorithm=ALGORITHM)


@dataclass
class CurrentUser:
    user: User
    role: str

    @property
    def email(self) -> str:
        return self.user.email


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> CurrentUser:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Login richiesto")
    try:
        payload = jwt.decode(creds.credentials, config.secret_key, algorithms=[ALGORITHM])
        user = db.get(User, int(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        user = None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sessione non valida o scaduta")
    # Il ruolo è ricalcolato a ogni richiesta: togliere un indirizzo dalla
    # whitelist revoca subito l'accesso.
    role = resolve_role(db, user.email)
    if role is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account non abilitato")
    return CurrentUser(user=user, role=role)


def require_user(cu: CurrentUser = Depends(current_user)) -> CurrentUser:
    return cu


def require_admin(cu: CurrentUser = Depends(current_user)) -> CurrentUser:
    if cu.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Riservato agli amministratori")
    return cu
