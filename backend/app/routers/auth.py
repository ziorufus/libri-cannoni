"""Login con servizi terzi (OAuth2 authorization code) e sessione via JWT.

Flusso:
  frontend  ->  GET /api/auth/login/{provider}?next=/admin
  backend   ->  redirect al provider (stato firmato in un cookie del backend)
  provider  ->  GET /api/auth/callback/{provider}?code=...&state=...
  backend   ->  verifica e-mail nella whitelist, crea il JWT e rimanda a
                {FRONTEND_URL}/auth/callback#token=...&next=/admin
"""

import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from ..access import normalize_email, resolve_role
from ..config import config
from ..db import get_db, utcnow
from ..models import User
from ..security import CurrentUser, create_token, current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])

STATE_COOKIE = "lc_oauth_state"
STATE_MAX_AGE = 600
_serializer = URLSafeTimedSerializer(config.secret_key, salt="oauth-state")


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    client_id: str
    client_secret: str
    authorize_url: str
    token_url: str
    scope: str


def _providers() -> dict[str, Provider]:
    tenant = config.microsoft_tenant
    all_ = [
        Provider(
            "google", "Google",
            config.google_client_id, config.google_client_secret,
            "https://accounts.google.com/o/oauth2/v2/auth",
            "https://oauth2.googleapis.com/token",
            "openid email profile",
        ),
        Provider(
            "microsoft", "Microsoft",
            config.microsoft_client_id, config.microsoft_client_secret,
            f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
            f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            "openid email profile User.Read",
        ),
    ]
    return {p.name: p for p in all_ if p.client_id and p.client_secret}


def _get_provider(name: str) -> Provider:
    provider = _providers().get(name)
    if provider is None:
        raise HTTPException(404, "Provider non configurato")
    return provider


def _redirect_uri(provider: str) -> str:
    return f"{config.backend_url.rstrip('/')}/api/auth/callback/{provider}"


def _safe_next(next_: str | None) -> str:
    if next_ and next_.startswith("/") and not next_.startswith("//"):
        return next_
    return "/"


def _frontend(path: str, query: dict | None = None, fragment: dict | None = None) -> RedirectResponse:
    url = config.frontend_origin + path
    if query:
        url += "?" + urlencode(query)
    if fragment:
        url += "#" + urlencode(fragment)
    resp = RedirectResponse(url, status_code=303)
    resp.delete_cookie(STATE_COOKIE, path="/api/auth")
    return resp


@router.get("/providers")
def list_providers():
    return [{"name": p.name, "label": p.label} for p in _providers().values()]


@router.get("/login/{provider}")
def login(provider: str, next: str | None = None):
    p = _get_provider(provider)
    state = secrets.token_urlsafe(24)
    params = {
        "client_id": p.client_id,
        "response_type": "code",
        "redirect_uri": _redirect_uri(p.name),
        "scope": p.scope,
        "state": state,
        "prompt": "select_account",
    }
    resp = RedirectResponse(f"{p.authorize_url}?{urlencode(params)}", status_code=303)
    resp.set_cookie(
        STATE_COOKIE,
        _serializer.dumps({"state": state, "provider": p.name, "next": _safe_next(next)}),
        max_age=STATE_MAX_AGE,
        httponly=True,
        secure=config.backend_url.startswith("https://"),
        samesite="lax",
        path="/api/auth",
    )
    return resp


async def _fetch_identity(p: Provider, code: str) -> tuple[str, str]:
    """Scambia il codice e restituisce (email, nome)."""
    async with httpx.AsyncClient(timeout=20) as client:
        tok = await client.post(
            p.token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _redirect_uri(p.name),
                "client_id": p.client_id,
                "client_secret": p.client_secret,
            },
            headers={"Accept": "application/json"},
        )
        tok.raise_for_status()
        headers = {"Authorization": f"Bearer {tok.json()['access_token']}"}

        if p.name == "google":
            r = await client.get("https://openidconnect.googleapis.com/v1/userinfo", headers=headers)
            r.raise_for_status()
            info = r.json()
            if not info.get("email") or not info.get("email_verified"):
                raise ValueError("L'account Google non ha un indirizzo e-mail verificato")
            return info["email"], info.get("name", "")

        # Microsoft: usiamo userPrincipalName e non "mail". Il campo "mail" è
        # modificabile dall'amministratore di qualsiasi tenant (attacco "nOAuth"),
        # mentre lo UPN deve appartenere a un dominio verificato del tenant.
        # Per gli account personali (outlook.com, ecc.) lo UPN è l'indirizzo e-mail.
        r = await client.get(
            "https://graph.microsoft.com/v1.0/me?$select=userPrincipalName,displayName",
            headers=headers,
        )
        r.raise_for_status()
        info = r.json()
        upn = info.get("userPrincipalName") or ""
        if "@" not in upn or "#EXT#" in upn:
            raise ValueError("Impossibile determinare l'indirizzo e-mail dell'account Microsoft")
        return upn, info.get("displayName", "")


@router.get("/callback/{provider}")
async def callback(
    provider: str,
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    p = _get_provider(provider)
    try:
        saved = _serializer.loads(request.cookies.get(STATE_COOKIE, ""), max_age=STATE_MAX_AGE)
    except BadSignature:
        return _frontend("/login", {"error": "state"})
    if error or not code:
        return _frontend("/login", {"error": "cancelled"})
    if saved.get("provider") != p.name or not secrets.compare_digest(saved.get("state", ""), state or ""):
        return _frontend("/login", {"error": "state"})

    try:
        email, name = await _fetch_identity(p, code)
    except (httpx.HTTPError, ValueError, KeyError):
        return _frontend("/login", {"error": "provider"})

    email = normalize_email(email)
    if resolve_role(db, email) is None:
        return _frontend("/login", {"error": "not_allowed", "email": email})

    user = db.query(User).filter_by(email=email).one_or_none()
    if user is None:
        user = User(email=email)
        db.add(user)
    user.name = name or user.name
    user.last_provider = p.name
    user.last_login_at = utcnow()
    db.commit()

    return _frontend("/auth/callback", fragment={"token": create_token(user), "next": saved["next"]})


@router.get("/me")
def me(cu: CurrentUser = Depends(current_user)):
    return {"email": cu.user.email, "name": cu.user.name, "role": cu.role}
