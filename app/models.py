from __future__ import annotations

from datetime import datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from .db import Base


class Owner(Base):
    __tablename__ = "owners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    mailboxes: Mapped[list["Mailbox"]] = relationship(back_populates="owner")
    statements: Mapped[list["StatementFile"]] = relationship(back_populates="owner")
    movements: Mapped[list["Movement"]] = relationship(back_populates="owner")
    transfers: Mapped[list["Transfer"]] = relationship(back_populates="owner")


class Mailbox(Base):
    __tablename__ = "mailboxes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owners.id"), index=True)
    provider: Mapped[str] = mapped_column(String(64), default="gmail")
    email_address: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    token_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    owner: Mapped["Owner"] = relationship(back_populates="mailboxes")
    email_messages: Mapped[list["EmailMessage"]] = relationship(back_populates="mailbox")
    statements: Mapped[list["StatementFile"]] = relationship(back_populates="mailbox")


class EmailMessage(Base):
    __tablename__ = "email_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mailbox_id: Mapped[int] = mapped_column(ForeignKey("mailboxes.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str] = mapped_column(String(500), default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    sender: Mapped[str] = mapped_column(String(500), default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    has_attachments: Mapped[bool] = mapped_column(default=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    mailbox: Mapped["Mailbox"] = relationship(back_populates="email_messages")
    statement_files: Mapped[list["StatementFile"]] = relationship(back_populates="email_message")
    transfers: Mapped[list["Transfer"]] = relationship(back_populates="email_message")


class StatementFile(Base):
    __tablename__ = "statement_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("owners.id"), nullable=True, index=True)
    mailbox_id: Mapped[int | None] = mapped_column(
        ForeignKey("mailboxes.id"), nullable=True, index=True
    )
    email_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_messages.id"), nullable=True, index=True
    )
    institution: Mapped[str] = mapped_column(String(128), index=True)
    source_type: Mapped[str] = mapped_column(String(64), default="upload")
    original_filename: Mapped[str] = mapped_column(String(500))
    stored_path: Mapped[str] = mapped_column(String(1000))
    file_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(64), default="parsed")
    detected_parser: Mapped[str] = mapped_column(String(128))
    statement_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    period_start: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    payment_due_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    min_payment: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_payment: Mapped[float | None] = mapped_column(Float, nullable=True)
    masked_account: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    owner: Mapped["Owner"] = relationship(back_populates="statements")
    mailbox: Mapped["Mailbox"] = relationship(back_populates="statements")
    email_message: Mapped["EmailMessage"] = relationship(back_populates="statement_files")
    imports: Mapped[list["StatementImport"]] = relationship(back_populates="statement_file")
    movements: Mapped[list["Movement"]] = relationship(back_populates="statement_file")


class StatementImport(Base):
    __tablename__ = "statement_imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    statement_file_id: Mapped[int] = mapped_column(ForeignKey("statement_files.id"), index=True)
    status: Mapped[str] = mapped_column(String(64), default="success")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    movement_count: Mapped[int] = mapped_column(Integer, default=0)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    statement_file: Mapped["StatementFile"] = relationship(back_populates="imports")


class Movement(Base):
    __tablename__ = "movements"
    __table_args__ = (UniqueConstraint("fingerprint", name="uq_movement_fingerprint"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("owners.id"), nullable=True, index=True)
    statement_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("statement_files.id"), nullable=True, index=True
    )
    email_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_messages.id"), nullable=True, index=True
    )
    institution: Mapped[str] = mapped_column(String(128), index=True)
    account_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    statement_period_start: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    statement_period_end: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(Date, nullable=True, index=True)
    description_raw: Mapped[str] = mapped_column(Text)
    description_normalized: Mapped[str] = mapped_column(Text)
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    movement_type: Mapped[str] = mapped_column(String(64), index=True)
    installment_info: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_type: Mapped[str] = mapped_column(String(64), default="statement")
    source_file: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    extra_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    owner: Mapped["Owner"] = relationship(back_populates="movements")
    statement_file: Mapped["StatementFile"] = relationship(back_populates="movements")
    reconciliation_links: Mapped[list["ReconciliationLink"]] = relationship(
        back_populates="movement", foreign_keys="ReconciliationLink.movement_id"
    )


class Transfer(Base):
    __tablename__ = "transfers"
    __table_args__ = (UniqueConstraint("fingerprint", name="uq_transfer_fingerprint"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("owners.id"), nullable=True, index=True)
    mailbox_id: Mapped[int | None] = mapped_column(
        ForeignKey("mailboxes.id"), nullable=True, index=True
    )
    email_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_messages.id"), nullable=True, index=True
    )
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    transfer_date: Mapped[datetime | None] = mapped_column(Date, nullable=True, index=True)
    description: Mapped[str] = mapped_column(Text)
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    direction: Mapped[str] = mapped_column(String(64), default="unknown")
    counterparty: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict)

    owner: Mapped["Owner"] = relationship(back_populates="transfers")
    email_message: Mapped["EmailMessage"] = relationship(back_populates="transfers")
    links: Mapped[list["ReconciliationLink"]] = relationship(back_populates="transfer")


class ReconciliationLink(Base):
    __tablename__ = "reconciliation_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    movement_id: Mapped[int | None] = mapped_column(
        ForeignKey("movements.id"), nullable=True, index=True
    )
    transfer_id: Mapped[int | None] = mapped_column(
        ForeignKey("transfers.id"), nullable=True, index=True
    )
    related_movement_id: Mapped[int | None] = mapped_column(
        ForeignKey("movements.id"), nullable=True, index=True
    )
    link_type: Mapped[str] = mapped_column(String(64), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    movement: Mapped["Movement"] = relationship(
        back_populates="reconciliation_links", foreign_keys=[movement_id]
    )
    transfer: Mapped["Transfer"] = relationship(back_populates="links")


class AgentSuggestion(Base):
    __tablename__ = "agent_suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(64), index=True)
    suggestion_type: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str] = mapped_column(String(64), index=True)
    target_id: Mapped[int] = mapped_column(Integer, index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    status: Mapped[str] = mapped_column(String(64), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
