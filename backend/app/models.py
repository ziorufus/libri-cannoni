from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, UTCDateTime, utcnow

# Valori ammessi per le condizioni del libro (NULL = non indicate: le prende Claude)
BOOK_CONDITIONS = ("ottime", "buone", "discrete")


class AccessEntry(Base):
    """Whitelist: un indirizzo e-mail esatto oppure un intero dominio."""

    __tablename__ = "access_entries"
    __table_args__ = (UniqueConstraint("kind", "value"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))  # "email" | "domain"
    value: Mapped[str] = mapped_column(String(320))  # minuscolo; dominio senza "@"
    role: Mapped[str] = mapped_column(String(10), default="user")  # "user" | "admin"
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class User(Base):
    """Account che hanno fatto almeno un login. Il ruolo si ricava dalla whitelist."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    last_provider: Mapped[str] = mapped_column(String(20), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Book(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Nome visualizzato ("Titolo - Autore"), ricavato dal JSON di Claude o scritto a mano
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    # Condizioni: uno di BOOK_CONDITIONS, oppure NULL ("usa valore di Claude")
    condition: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_by: Mapped[str] = mapped_column(String(320), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    # Avanzamento nelle fasi (vedi app/phases.py)
    phase: Mapped[int] = mapped_column(Integer, default=0, index=True)
    # ready | running | waiting | failed | idle
    status: Mapped[str] = mapped_column(String(10), default="ready", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")

    # Lock ("lease") per il processo in background: un solo worker alla volta
    lease_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    # Risultati delle fasi
    claude_output: Mapped[str | None] = mapped_column(Text, nullable=True)

    photos: Mapped[list["Photo"]] = relationship(
        back_populates="book",
        order_by="Photo.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    runs: Mapped[list["PhaseRun"]] = relationship(
        back_populates="book",
        order_by="PhaseRun.id.desc()",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Photo(Base):
    __tablename__ = "photos"
    __table_args__ = (UniqueConstraint("book_id", "position"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)  # 1, 2, 3, ... (ordine di scatto)
    filename: Mapped[str] = mapped_column(String(100))
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)

    book: Mapped[Book] = relationship(back_populates="photos")


class PhaseRun(Base):
    """Storico di ogni tentativo eseguito dal processo in background."""

    __tablename__ = "phase_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    phase: Mapped[int] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer)
    worker: Mapped[str] = mapped_column(String(100), default="")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    output: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")

    book: Mapped[Book] = relationship(back_populates="runs")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
