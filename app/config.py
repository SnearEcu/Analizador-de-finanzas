from __future__ import annotations

import os
import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = BASE_DIR / "app"
STORAGE_DIR = BASE_DIR / "storage"
UPLOADS_DIR = STORAGE_DIR / "statements"
TOKENS_DIR = STORAGE_DIR / "tokens"
OCR_CACHE_DIR = STORAGE_DIR / "ocr-cache"
DB_PATH = STORAGE_DIR / "finance.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

APP_BASE_URL = os.getenv("FINANCE_APP_BASE_URL", "http://127.0.0.1:8000")
SECRET_KEY = os.getenv("FINANCE_APP_SECRET", "change-me-in-production")
GOOGLE_CLIENT_SECRETS_FILE = Path(
    os.getenv(
        "GOOGLE_CLIENT_SECRETS_FILE",
        str(BASE_DIR / "credentials" / "google-client-secret.json"),
    )
)
GOOGLE_SIMPLE_OAUTH_FILE = Path(
    os.getenv(
        "GOOGLE_SIMPLE_OAUTH_FILE",
        str(BASE_DIR / "credentials" / "google-oauth-client.json"),
    )
)
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.readonly",
]

DEFAULT_CURRENCY = "USD"


def ensure_directories() -> None:
    for path in (STORAGE_DIR, UPLOADS_DIR, TOKENS_DIR, OCR_CACHE_DIR):
        path.mkdir(parents=True, exist_ok=True)


def load_google_client_config() -> dict:
    if GOOGLE_CLIENT_SECRETS_FILE.exists():
        return json.loads(GOOGLE_CLIENT_SECRETS_FILE.read_text(encoding="utf-8"))
    if GOOGLE_SIMPLE_OAUTH_FILE.exists():
        raw = json.loads(GOOGLE_SIMPLE_OAUTH_FILE.read_text(encoding="utf-8"))
        client_id = raw.get("client_id")
        client_secret = raw.get("client_secret")
        project_id = raw.get("project_id", "analizador-finanzas")
    elif GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
        client_id = GOOGLE_CLIENT_ID
        client_secret = GOOGLE_CLIENT_SECRET
        project_id = "analizador-finanzas"
    else:
        raise FileNotFoundError(
            "Falta la configuración OAuth de Google. Usa "
            f"{GOOGLE_CLIENT_SECRETS_FILE} o {GOOGLE_SIMPLE_OAUTH_FILE}."
        )
    return {
        "installed": {
            "client_id": client_id,
            "project_id": project_id,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": client_secret,
            "redirect_uris": [
                "http://localhost",
                "http://127.0.0.1:8000/auth/google/callback",
            ],
        }
    }
