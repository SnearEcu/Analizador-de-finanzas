from __future__ import annotations

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .config import APP_DIR, BASE_DIR, ensure_directories
from .db import get_session, init_db
from .models import Mailbox
from .schemas import GmailSyncRequest, GoogleAuthStartRequest
from .services import (
    complete_google_auth,
    get_agent_suggestions,
    get_mailboxes,
    get_movements,
    get_statements,
    get_summary,
    import_uploaded_statement,
    run_reconciliation,
    start_google_auth,
    sync_gmail_mailbox,
)


ensure_directories()
init_db()

app = FastAPI(title="Analizador Financiero Familiar")
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


def _static_version() -> str:
    files = [
        APP_DIR / "static" / "css" / "app.css",
        APP_DIR / "static" / "js" / "app.js",
        APP_DIR / "templates" / "base.html",
        APP_DIR / "templates" / "index.html",
    ]
    latest = max(int(path.stat().st_mtime) for path in files if path.exists())
    return str(latest)
@app.get("/", response_class=HTMLResponse)
def index(request: Request, session: Session = Depends(get_session)):
    mailbox_map = {mailbox.email_address: mailbox.id for mailbox in session.query(Mailbox).all()}
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "mailboxes": mailbox_map,
            "static_version": _static_version(),
            "summary_endpoint": "/api/dashboard/summary",
            "statements_endpoint": "/api/statements",
            "movements_endpoint": "/api/movements",
            "suggestions_endpoint": "/api/agent-suggestions",
            "mailboxes_endpoint": "/api/mailboxes",
            "gmail_connect_endpoint": "/api/auth/google/start",
            "gmail_sync_endpoint": "/api/ingest/gmail/sync",
            "upload_statement_endpoint": "/api/ingest/upload-statement",
        },
    )


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/auth/google/start")
@app.get("/api/auth/google/start")
@app.post("/auth/google/start")
@app.post("/api/auth/google/start")
def auth_google_start(
    request: Request,
    payload: GoogleAuthStartRequest | None = None,
    owner_name: str | None = Query(default=None),
    email_address: str | None = Query(default=None),
    mailbox: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    if payload:
        owner = payload.owner_name
        email = payload.email_address
    else:
        owner = owner_name or ("Bryan Ortega" if mailbox == "owner" else "Sheerlayn Chiriboga")
        email = email_address
    if not email:
        raise HTTPException(status_code=400, detail="Debes enviar owner_name y email_address")
    try:
        authorization_url = start_google_auth(session, owner, email)
        session.commit()
        if request.method == "GET":
            return RedirectResponse(url=authorization_url, status_code=302)
        return {"authorization_url": authorization_url}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/auth/google/callback")
def auth_google_callback(
    state: str,
    code: str,
    session: Session = Depends(get_session),
):
    try:
        mailbox = complete_google_auth(session, state, code)
        session.commit()
        return RedirectResponse(url=f"/?connected={mailbox.id}", status_code=302)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        return RedirectResponse(url=f"/?oauth_error={str(exc)}", status_code=302)


def _resolve_mailbox_id(session: Session, mailbox_id: int | None, mailbox: str | None) -> int:
    if mailbox_id:
        return mailbox_id
    if mailbox:
        rows = session.query(Mailbox).order_by(Mailbox.id.asc()).all()
        if mailbox == "owner" and rows:
            return rows[0].id
        if mailbox == "spouse" and len(rows) > 1:
            return rows[1].id
    rows = session.query(Mailbox).order_by(Mailbox.id.asc()).all()
    if len(rows) == 1:
        return rows[0].id
    raise HTTPException(status_code=400, detail="Mailbox no especificado o no conectado")


@app.post("/ingest/gmail/sync")
@app.post("/api/ingest/gmail/sync")
async def ingest_gmail_sync(
    request_http: Request,
    request: GmailSyncRequest | None = None,
    mailbox_id: int | None = Form(default=None),
    mailbox: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    try:
        max_results = request.max_results if request else 100
        if request is None and request_http.headers.get("content-type", "").startswith("application/json"):
            try:
                body = await request_http.json()
            except Exception:
                body = {}
            if isinstance(body, dict):
                mailbox = body.get("mailbox", mailbox)
                mailbox_id = body.get("mailbox_id", mailbox_id)
                if body.get("max_results"):
                    max_results = int(body["max_results"])
        resolved_mailbox_id = _resolve_mailbox_id(
            session,
            request.mailbox_id if request else mailbox_id,
            mailbox,
        )
        result = sync_gmail_mailbox(
            session=session,
            mailbox_id=resolved_mailbox_id,
            max_results=max_results or 100,
        )
        session.commit()
        return result
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/ingest/upload-statement")
@app.post("/api/ingest/upload-statement")
async def ingest_upload_statement(
    file: UploadFile = File(...),
    owner_name: str | None = Form(default=None),
    session: Session = Depends(get_session),
):
    try:
        data = await file.read()
        statement = import_uploaded_statement(session, file.filename, data)
        if owner_name and statement.owner_id is None:
            pass
        session.commit()
        return {
            "statement_id": statement.id,
            "institution": statement.institution,
            "detected_parser": statement.detected_parser,
            "movement_count": len(statement.movements),
            "owner_name": statement.owner.name if statement.owner else owner_name,
        }
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/statements")
@app.get("/api/statements")
def list_statements(session: Session = Depends(get_session)):
    return get_statements(session)


@app.get("/movements")
@app.get("/api/movements")
def list_movements(
    owner: str | None = Query(default=None),
    institution: str | None = Query(default=None),
    movement_type: str | None = Query(default=None),
    search: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    return get_movements(
        session,
        filters={
            "owner": owner,
            "institution": institution,
            "movement_type": movement_type,
            "search": search,
        },
    )


@app.get("/dashboard/summary")
@app.get("/api/dashboard/summary")
def dashboard_summary(session: Session = Depends(get_session)):
    return get_summary(session)


@app.post("/reconciliation/run")
@app.post("/api/reconciliation/run")
def reconciliation_run(session: Session = Depends(get_session)):
    try:
        result = run_reconciliation(session)
        session.commit()
        return result
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/agent-suggestions")
def list_agent_suggestions(session: Session = Depends(get_session)):
    return get_agent_suggestions(session)


@app.get("/api/mailboxes")
def list_mailboxes(session: Session = Depends(get_session)):
    return get_mailboxes(session)


@app.post("/bootstrap/import-samples")
def bootstrap_import_samples(session: Session = Depends(get_session)):
    sample_files = sorted(BASE_DIR.glob("*.pdf"))
    imported = []
    try:
        for path in sample_files:
            data = path.read_bytes()
            statement = import_uploaded_statement(session, path.name, data, source_type="seed")
            imported.append(statement.id)
        session.commit()
        return {"imported_statement_ids": imported}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
