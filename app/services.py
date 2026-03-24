from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import Flow
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from .config import APP_BASE_URL, GOOGLE_SCOPES, SECRET_KEY, TOKENS_DIR, UPLOADS_DIR, load_google_client_config
from .models import AgentSuggestion, EmailMessage, Mailbox, Movement, Owner, ReconciliationLink, StatementFile, StatementImport, Transfer
from .parsers import ParsedStatement, classify_category, normalize_description, parse_amount, parse_statement, slugify


serializer = URLSafeSerializer(SECRET_KEY, salt="finance-auth")


def mask_account(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"[^0-9Xx]", "", value)
    if len(digits) <= 4:
        return value
    return f"{digits[:4]}...{digits[-4:]}"


def ensure_owner(session: Session, owner_name: str | None) -> Owner | None:
    if not owner_name:
        return None
    owner_name = " ".join(owner_name.split()).strip()
    slug = slugify(owner_name)
    owner = session.scalar(select(Owner).where(Owner.slug == slug))
    if owner:
        aliases = set(owner.aliases or [])
        aliases.add(owner_name)
        owner.aliases = sorted(aliases)
        return owner
    owner = Owner(name=owner_name, slug=slug, aliases=[owner_name])
    session.add(owner)
    session.flush()
    return owner


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_fingerprint(*parts: Any) -> str:
    raw = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def store_statement_file(original_filename: str, data: bytes) -> tuple[Path, str]:
    file_hash = compute_sha256(data)
    destination = UPLOADS_DIR / f"{file_hash}{Path(original_filename).suffix.lower() or '.pdf'}"
    if not destination.exists():
        destination.write_bytes(data)
    return destination, file_hash


def create_mailbox(session: Session, owner_name: str, email_address: str) -> Mailbox:
    owner = ensure_owner(session, owner_name)
    mailbox = session.scalar(select(Mailbox).where(Mailbox.email_address == email_address))
    if mailbox:
        mailbox.owner_id = owner.id if owner else mailbox.owner_id
        session.flush()
        return mailbox
    mailbox = Mailbox(owner_id=owner.id if owner else None, email_address=email_address, provider="gmail")
    session.add(mailbox)
    session.flush()
    return mailbox


def start_google_auth(session: Session, owner_name: str, email_address: str) -> str:
    mailbox = create_mailbox(session, owner_name, email_address)
    flow = Flow.from_client_config(
        load_google_client_config(),
        scopes=GOOGLE_SCOPES,
        redirect_uri=f"{APP_BASE_URL}/auth/google/callback",
    )
    code_verifier = secrets.token_urlsafe(64)
    flow.code_verifier = code_verifier
    state = serializer.dumps({"mailbox_id": mailbox.id, "code_verifier": code_verifier})
    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return authorization_url


def complete_google_auth(session: Session, state: str, code: str) -> Mailbox:
    try:
        payload = serializer.loads(state)
    except BadSignature as exc:
        raise ValueError("Estado OAuth inválido") from exc
    mailbox = session.get(Mailbox, payload["mailbox_id"])
    if not mailbox:
        raise ValueError("Mailbox no encontrado")
    flow = Flow.from_client_config(
        load_google_client_config(),
        scopes=GOOGLE_SCOPES,
        redirect_uri=f"{APP_BASE_URL}/auth/google/callback",
        state=state,
    )
    if payload.get("code_verifier"):
        flow.code_verifier = payload["code_verifier"]
    flow.fetch_token(code=code)
    token_path = TOKENS_DIR / f"gmail-{mailbox.id}.json"
    token_path.write_text(flow.credentials.to_json(), encoding="utf-8")
    mailbox.token_path = str(token_path)
    session.flush()
    return mailbox


def load_mailbox_credentials(mailbox: Mailbox) -> Credentials:
    if not mailbox.token_path:
        raise FileNotFoundError("El mailbox no tiene token OAuth almacenado")
    token_path = Path(mailbox.token_path)
    if not token_path.exists():
        raise FileNotFoundError(f"No existe el token OAuth: {token_path}")
    return Credentials.from_authorized_user_file(str(token_path), scopes=GOOGLE_SCOPES)


def extract_headers(payload: dict) -> dict[str, str]:
    headers = payload.get("headers") or []
    return {header.get("name", "").lower(): header.get("value", "") for header in headers}


def iter_parts(payload: dict):
    stack = [payload]
    while stack:
        current = stack.pop()
        yield current
        for part in current.get("parts", []) or []:
            stack.append(part)


def decode_message_body(payload: dict) -> str:
    for part in iter_parts(payload):
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data")
            if data:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", "ignore")
    return ""


def detect_transfer(subject: str, snippet: str, body: str, sent_at: datetime | None) -> dict | None:
    combined = " ".join(filter(None, [subject, snippet, body]))
    lowered = combined.lower()
    if not any(word in lowered for word in ["transfer", "transferencia", "enviaste", "recibiste"]):
        return None
    amount_match = re.search(r"(?:usd|\$)\s*([\d\.,]+)|([\d\.,]+)\s*usd", combined, re.IGNORECASE)
    amount_raw = amount_match.group(1) or amount_match.group(2) if amount_match else None
    amount = parse_amount(amount_raw)
    if amount is None:
        return None
    direction = "outgoing" if any(word in lowered for word in ["enviaste", "transferiste"]) else "incoming"
    counterparty_match = re.search(r"(?:a|de)\s+([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ ]{3,})", combined)
    return {
        "amount": abs(amount),
        "direction": direction,
        "counterparty": counterparty_match.group(1).strip() if counterparty_match else None,
        "transfer_date": sent_at.date() if sent_at else None,
    }


def add_router_suggestion(session: Session, statement: StatementFile, parsed: ParsedStatement) -> None:
    exists = session.scalar(
        select(AgentSuggestion).where(
            AgentSuggestion.agent_name == "router",
            AgentSuggestion.target_type == "statement",
            AgentSuggestion.target_id == statement.id,
        )
    )
    if exists:
        return
    session.add(
        AgentSuggestion(
            agent_name="router",
            suggestion_type="parser_selection",
            target_type="statement",
            target_id=statement.id,
            payload_json={"institution": parsed.institution, "parser": parsed.parser_name},
            confidence=0.99,
            status="accepted",
        )
    )


def add_category_suggestion(session: Session, movement: Movement) -> None:
    if not movement.category:
        return
    exists = session.scalar(
        select(AgentSuggestion).where(
            AgentSuggestion.agent_name == "categorizer",
            AgentSuggestion.target_type == "movement",
            AgentSuggestion.target_id == movement.id,
            AgentSuggestion.suggestion_type == "category",
        )
    )
    if exists:
        return
    session.add(
        AgentSuggestion(
            agent_name="categorizer",
            suggestion_type="category",
            target_type="movement",
            target_id=movement.id,
            payload_json={"category": movement.category, "description": movement.description_raw},
            confidence=0.72,
        )
    )


def persist_parsed_statement(
    session: Session,
    parsed: ParsedStatement,
    stored_path: Path,
    file_hash: str,
    source_type: str,
    mailbox: Mailbox | None = None,
    email_message: EmailMessage | None = None,
) -> StatementFile:
    existing = session.scalar(select(StatementFile).where(StatementFile.file_hash == file_hash))
    if existing:
        return existing
    owner = ensure_owner(session, parsed.owner_name)
    statement = StatementFile(
        owner_id=owner.id if owner else None,
        mailbox_id=mailbox.id if mailbox else None,
        email_message_id=email_message.id if email_message else None,
        institution=parsed.institution,
        source_type=source_type,
        original_filename=parsed.original_filename,
        stored_path=str(stored_path),
        file_hash=file_hash,
        status="parsed",
        detected_parser=parsed.parser_name,
        statement_date=parsed.statement_date,
        period_start=parsed.period_start,
        period_end=parsed.period_end,
        payment_due_date=parsed.payment_due_date,
        min_payment=parsed.min_payment,
        total_payment=parsed.total_payment,
        masked_account=mask_account(parsed.masked_account),
        metadata_json=parsed.metadata,
    )
    session.add(statement)
    session.flush()
    add_router_suggestion(session, statement, parsed)
    imported_count = 0
    for movement in parsed.movements:
        movement_owner = ensure_owner(session, movement.owner_name or parsed.owner_name)
        fingerprint = build_fingerprint(
            parsed.institution,
            movement.account_label,
            movement.posted_at,
            round(movement.amount, 2),
            normalize_description(movement.description_raw),
        )
        record = Movement(
            owner_id=movement_owner.id if movement_owner else owner.id if owner else None,
            statement_file_id=statement.id,
            email_message_id=email_message.id if email_message else None,
            institution=parsed.institution,
            account_label=mask_account(movement.account_label or parsed.masked_account),
            statement_period_start=parsed.period_start,
            statement_period_end=parsed.period_end,
            posted_at=movement.posted_at,
            description_raw=movement.description_raw,
            description_normalized=normalize_description(movement.description_raw),
            amount=movement.amount,
            currency=movement.currency,
            movement_type=movement.movement_type,
            installment_info=movement.installment_info,
            source_type=source_type,
            source_file=str(stored_path),
            source_message_id=email_message.external_id if email_message else None,
            confidence=movement.confidence,
            category=classify_category(movement.description_raw),
            fingerprint=fingerprint,
            extra_json=movement.metadata,
        )
        session.add(record)
        try:
            session.flush()
            imported_count += 1
            add_category_suggestion(session, record)
        except IntegrityError:
            session.rollback()
            statement = session.scalar(select(StatementFile).where(StatementFile.file_hash == file_hash))
            if statement is None:
                raise
    session.add(StatementImport(statement_file_id=statement.id, status="success", movement_count=imported_count))
    session.flush()
    return statement


def persist_skipped_statement(
    session: Session,
    filename: str,
    stored_path: Path,
    file_hash: str,
    source_type: str,
    reason: str,
    mailbox: Mailbox | None = None,
    email_message: EmailMessage | None = None,
) -> StatementFile:
    existing = session.scalar(select(StatementFile).where(StatementFile.file_hash == file_hash))
    if existing:
        return existing
    statement = StatementFile(
        owner_id=mailbox.owner_id if mailbox else None,
        mailbox_id=mailbox.id if mailbox else None,
        email_message_id=email_message.id if email_message else None,
        institution="unsupported",
        source_type=source_type,
        original_filename=filename,
        stored_path=str(stored_path),
        file_hash=file_hash,
        status="skipped",
        detected_parser="unsupported",
        metadata_json={"reason": reason},
    )
    session.add(statement)
    session.flush()
    session.add(
        StatementImport(
            statement_file_id=statement.id,
            status="skipped",
            error_message=reason,
            movement_count=0,
        )
    )
    session.flush()
    return statement


def import_uploaded_statement(session: Session, filename: str, data: bytes, source_type: str = "upload") -> StatementFile:
    stored_path, file_hash = store_statement_file(filename, data)
    parsed = parse_statement(stored_path)
    parsed.original_filename = filename
    return persist_parsed_statement(session, parsed, stored_path, file_hash, source_type=source_type)


def sync_gmail_mailbox(session: Session, mailbox_id: int, max_results: int = 100) -> dict:
    mailbox = session.get(Mailbox, mailbox_id)
    if not mailbox:
        raise ValueError("Mailbox no encontrado")
    credentials = load_mailbox_credentials(mailbox)
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    query = "newer_than:365d"
    try:
        response = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    except HttpError as exc:
        raise RuntimeError(f"Error consultando Gmail: {exc}") from exc
    imported_statements = 0
    detected_transfers = 0
    skipped_statements = 0
    scanned_messages = 0
    duplicate_statements = 0
    pdf_attachments_found = 0
    for message_meta in response.get("messages", []):
        scanned_messages += 1
        message = service.users().messages().get(userId="me", id=message_meta["id"], format="full").execute()
        payload = message.get("payload", {})
        headers = extract_headers(payload)
        sent_at = None
        if headers.get("date"):
            try:
                sent_at = parsedate_to_datetime(headers["date"])
            except Exception:
                sent_at = None
        email_message = session.scalar(select(EmailMessage).where(EmailMessage.external_id == message["id"]))
        if not email_message:
            email_message = EmailMessage(
                mailbox_id=mailbox.id,
                external_id=message["id"],
                thread_id=message.get("threadId"),
                subject=headers.get("subject", ""),
                snippet=message.get("snippet", ""),
                sender=headers.get("from", ""),
                sent_at=sent_at,
                has_attachments=any(part.get("filename") for part in iter_parts(payload)),
                metadata_json={"labelIds": message.get("labelIds", [])},
            )
            session.add(email_message)
            session.flush()
        body = decode_message_body(payload)
        transfer = detect_transfer(email_message.subject, email_message.snippet, body, sent_at)
        if transfer:
            fingerprint = build_fingerprint(mailbox.id, email_message.external_id, transfer["transfer_date"], transfer["amount"], transfer["direction"])
            if not session.scalar(select(Transfer).where(Transfer.fingerprint == fingerprint)):
                session.add(
                    Transfer(
                        owner_id=mailbox.owner_id,
                        mailbox_id=mailbox.id,
                        email_message_id=email_message.id,
                        transfer_date=transfer["transfer_date"],
                        description=f"{email_message.subject} {email_message.snippet}".strip(),
                        amount=transfer["amount"],
                        direction=transfer["direction"],
                        counterparty=transfer["counterparty"],
                        confidence=0.65,
                        fingerprint=fingerprint,
                        raw_payload={"subject": email_message.subject, "snippet": email_message.snippet},
                    )
                )
                detected_transfers += 1
        for part in iter_parts(payload):
            filename = part.get("filename") or ""
            attachment_id = part.get("body", {}).get("attachmentId")
            if not filename.lower().endswith(".pdf") or not attachment_id:
                continue
            pdf_attachments_found += 1
            attachment = service.users().messages().attachments().get(userId="me", messageId=message["id"], id=attachment_id).execute()
            data = base64.urlsafe_b64decode(attachment["data"] + "==")
            stored_path, file_hash = store_statement_file(filename, data)
            if session.scalar(select(StatementFile).where(StatementFile.file_hash == file_hash)):
                duplicate_statements += 1
                continue
            try:
                parsed = parse_statement(stored_path)
                parsed.original_filename = filename
                persist_parsed_statement(session, parsed, stored_path, file_hash, source_type="gmail", mailbox=mailbox, email_message=email_message)
                imported_statements += 1
            except ValueError as exc:
                persist_skipped_statement(
                    session=session,
                    filename=filename,
                    stored_path=stored_path,
                    file_hash=file_hash,
                    source_type="gmail",
                    reason=str(exc),
                    mailbox=mailbox,
                    email_message=email_message,
                )
                skipped_statements += 1
    mailbox.last_synced_at = datetime.utcnow()
    session.flush()
    return {
        "mailbox_id": mailbox.id,
        "email_address": mailbox.email_address,
        "scanned_messages": scanned_messages,
        "pdf_attachments_found": pdf_attachments_found,
        "imported_statements": imported_statements,
        "duplicate_statements": duplicate_statements,
        "skipped_statements": skipped_statements,
        "detected_transfers": detected_transfers,
    }


def run_reconciliation(session: Session) -> dict:
    candidates = session.scalars(select(Transfer)).all()
    created = 0
    for transfer in candidates:
        linked = session.scalar(select(ReconciliationLink).where(ReconciliationLink.transfer_id == transfer.id))
        if linked or transfer.transfer_date is None:
            continue
        date_min = transfer.transfer_date - timedelta(days=3)
        date_max = transfer.transfer_date + timedelta(days=3)
        movement = session.scalar(
            select(Movement)
            .where(
                Movement.owner_id == transfer.owner_id,
                Movement.posted_at >= date_min,
                Movement.posted_at <= date_max,
                func.abs(func.abs(Movement.amount) - abs(transfer.amount)) < 0.01,
            )
            .order_by(Movement.posted_at.asc())
        )
        if not movement:
            continue
        session.add(ReconciliationLink(movement_id=movement.id, transfer_id=transfer.id, link_type="transfer_match", confidence=0.78, note="Coincidencia por monto y ventana de fechas"))
        session.add(
            AgentSuggestion(
                agent_name="reconciler",
                suggestion_type="transfer_match",
                target_type="movement",
                target_id=movement.id,
                payload_json={"transfer_id": transfer.id, "amount": transfer.amount, "transfer_date": str(transfer.transfer_date)},
                confidence=0.78,
                status="pending",
            )
        )
        created += 1
    session.flush()
    return {"created_links": created}


def get_summary(session: Session) -> dict:
    movements = session.scalars(select(Movement)).all()
    statements = session.scalars(select(StatementFile)).all()
    transfers = session.scalars(select(Transfer)).all()
    expense_total = round(sum(m.amount for m in movements if m.amount > 0 and m.movement_type in {"consumption", "fee"}), 2)
    payment_total = round(abs(sum(m.amount for m in movements if m.movement_type == "payment")), 2)
    income_total = round(sum(abs(m.amount) for m in movements if m.amount < 0 and m.movement_type == "refund"), 2)
    transfer_total = round(sum(t.amount for t in transfers), 2)
    net_total = round(income_total - expense_total - payment_total, 2)
    return {
        "income_total": income_total,
        "expense_total": expense_total,
        "payment_total": payment_total,
        "transfer_total": transfer_total,
        "net_total": net_total,
        "period_label": datetime.utcnow().strftime("%B %Y"),
        "source_label": "Carga manual + Gmail",
        "total_movements": len(movements),
        "total_estados": len(statements),
        "total_transferencias": len(transfers),
    }


def get_statements(session: Session) -> list[dict]:
    rows = session.scalars(select(StatementFile).options(joinedload(StatementFile.owner)).order_by(StatementFile.created_at.desc())).all()
    items = []
    for row in rows:
        items.append(
            {
                "id": row.id,
                "institution": row.institution,
                "institution_label": {"diners": "Diners Titanium", "internacional": "Banco Internacional", "pacifico": "Banco del Pacífico"}.get(row.institution, row.institution),
                "original_filename": row.original_filename,
                "source_type": row.source_type,
                "period_start": str(row.period_start) if row.period_start else None,
                "period_end": str(row.period_end) if row.period_end else None,
                "period_label": f"{row.period_start} - {row.period_end}" if row.period_start and row.period_end else None,
                "statement_date": str(row.statement_date) if row.statement_date else None,
                "cutoff_date": str(row.statement_date) if row.statement_date else None,
                "payment_due_date": str(row.payment_due_date) if row.payment_due_date else None,
                "min_payment": row.min_payment,
                "minimum_payment": row.min_payment,
                "total_payment": row.total_payment,
                "masked_account": row.masked_account,
                "owner_name": row.owner.name if row.owner else None,
                "owner_label": row.owner.name if row.owner else None,
                "created_at": row.created_at.isoformat(),
            }
        )
    return items


def get_movements(session: Session, filters: dict | None = None) -> list[dict]:
    filters = filters or {}
    query = select(Movement).options(joinedload(Movement.owner)).order_by(Movement.posted_at.desc(), Movement.id.desc())
    if filters.get("owner"):
        query = query.join(Owner, Movement.owner_id == Owner.id).where(or_(Owner.slug == filters["owner"], Owner.name.ilike(f"%{filters['owner']}%")))
    if filters.get("institution"):
        query = query.where(Movement.institution == filters["institution"])
    if filters.get("movement_type"):
        query = query.where(Movement.movement_type == filters["movement_type"])
    if filters.get("search"):
        search = f"%{filters['search']}%"
        query = query.where(or_(Movement.description_raw.ilike(search), Movement.account_label.ilike(search), Movement.category.ilike(search)))
    rows = session.scalars(query).all()
    items = []
    for row in rows:
        items.append(
            {
                "id": row.id,
                "owner": row.owner.slug if row.owner else None,
                "owner_name": row.owner.name if row.owner else None,
                "owner_label": row.owner.name if row.owner else None,
                "institution": row.institution,
                "institution_label": {"diners": "Diners Titanium", "internacional": "Banco Internacional", "pacifico": "Banco del Pacífico"}.get(row.institution, row.institution),
                "account_label": row.account_label,
                "posted_at": str(row.posted_at) if row.posted_at else None,
                "description_raw": row.description_raw,
                "amount": row.amount,
                "movement_type": row.movement_type,
                "category": row.category,
                "source_type": row.source_type,
                "confidence": row.confidence,
            }
        )
    return items


def get_agent_suggestions(session: Session) -> list[dict]:
    rows = session.scalars(select(AgentSuggestion).order_by(AgentSuggestion.created_at.desc())).all()
    items = []
    for row in rows:
        payload = row.payload_json or {}
        items.append(
            {
                "id": row.id,
                "agent_name": row.agent_name,
                "suggestion_type": row.suggestion_type,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "suggestion_label": payload.get("category") or payload.get("parser") or payload.get("transfer_id") or json.dumps(payload, ensure_ascii=False),
                "source_ref": f"{row.target_type}:{row.target_id}",
                "confidence": row.confidence,
                "status": row.status,
            }
        )
    return items


def get_mailboxes(session: Session) -> list[dict]:
    rows = session.scalars(
        select(Mailbox).options(joinedload(Mailbox.owner)).order_by(Mailbox.id.asc())
    ).all()
    items = []
    for index, row in enumerate(rows, start=1):
        role = "owner" if index == 1 else "spouse" if index == 2 else f"mailbox-{row.id}"
        connected = bool(row.token_path and Path(row.token_path).exists())
        items.append(
            {
                "id": row.id,
                "role": role,
                "email_address": row.email_address,
                "owner_name": row.owner.name if row.owner else None,
                "provider": row.provider,
                "connected": connected,
                "last_synced_at": row.last_synced_at.isoformat() if row.last_synced_at else None,
            }
        )
    return items
