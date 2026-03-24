from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class GoogleAuthStartRequest(BaseModel):
    owner_name: str
    email_address: str


class GmailSyncRequest(BaseModel):
    mailbox_id: int
    max_results: int = Field(default=100, ge=1, le=200)


class UploadStatementResponse(BaseModel):
    statement_id: int
    institution: str
    detected_parser: str
    movement_count: int
    owner_name: str | None


class SummaryResponse(BaseModel):
    total_gastos: float
    total_pagos: float
    total_transferencias: float
    balance_neto: float
    total_movimientos: int
    total_estados: int


class StatementOut(BaseModel):
    id: int
    institution: str
    original_filename: str
    source_type: str
    period_start: date | None
    period_end: date | None
    statement_date: date | None
    payment_due_date: date | None
    min_payment: float | None
    total_payment: float | None
    masked_account: str | None
    owner_name: str | None
    created_at: datetime


class MovementOut(BaseModel):
    id: int
    owner_name: str | None
    institution: str
    account_label: str | None
    posted_at: date | None
    description_raw: str
    amount: float
    movement_type: str
    category: str | None
    source_type: str
    confidence: float
