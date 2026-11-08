"""Whitelist di accesso: e-mail esatte o interi domini."""

from sqlalchemy import or_
from sqlalchemy.orm import Session

from .models import AccessEntry

ROLE_RANK = {"user": 1, "admin": 2}


def normalize_email(email: str) -> str:
    return email.strip().lower()


def normalize_domain(domain: str) -> str:
    return domain.strip().lower().lstrip("@")


def resolve_role(db: Session, email: str) -> str | None:
    """Ruolo dell'utente secondo la whitelist; None = non abilitato.

    Se più voci corrispondono (e-mail e dominio) vale il ruolo più alto.
    """
    email = normalize_email(email)
    if "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1]
    entries = (
        db.query(AccessEntry)
        .filter(
            or_(
                (AccessEntry.kind == "email") & (AccessEntry.value == email),
                (AccessEntry.kind == "domain") & (AccessEntry.value == domain),
            )
        )
        .all()
    )
    if not entries:
        return None
    return max((e.role for e in entries), key=lambda r: ROLE_RANK.get(r, 0))


def ensure_admin(db: Session, email: str) -> None:
    email = normalize_email(email)
    entry = db.query(AccessEntry).filter_by(kind="email", value=email).one_or_none()
    if entry is None:
        db.add(AccessEntry(kind="email", value=email, role="admin", note="ADMIN_EMAIL"))
    elif entry.role != "admin":
        entry.role = "admin"
    db.commit()
