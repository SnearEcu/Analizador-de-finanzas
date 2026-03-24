# Analizador Financiero Familiar

App web local en FastAPI para importar estados de cuenta, consolidar movimientos por titular y sincronizar correos Gmail con adjuntos y transferencias.

## Qué hace esta V1

- Importa PDFs manualmente y los normaliza a un modelo único de movimientos.
- Trae parsers iniciales para:
  - `Diners Titanium` por texto PDF
  - `Banco Internacional` por texto PDF
  - `Banco del Pacífico` por OCR
- Separa movimientos por titular y mantiene vista familiar consolidada.
- Guarda evidencia de origen por archivo y registra sugerencias de router, categorización y conciliación.
- Expone endpoints para OAuth Gmail, sincronización de correo, dashboard, estados, movimientos y conciliación.

## Requisitos

- Python 3.12+
- Dependencias de `requirements.txt`
- Para Gmail:
  - un archivo OAuth de Google en `credentials/google-client-secret.json`
  - o un archivo simple en `credentials/google-oauth-client.json`
  - `redirect_uri` configurado en Google como `http://127.0.0.1:8000/auth/google/callback`

## Instalación

```powershell
python -m pip install -r requirements.txt
```

## Ejecutar

```powershell
uvicorn app.main:app --reload
```

Abrir [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Configuración simple de Gmail

Si Google no te deja descargar el JSON clásico, crea este archivo:

`credentials/google-oauth-client.json`

con este formato:

```json
{
  "client_id": "TU_CLIENT_ID.apps.googleusercontent.com",
  "client_secret": "TU_CLIENT_SECRET",
  "project_id": "analizador-finanzas"
}
```

## Flujo rápido

1. Conecta un Gmail desde el botón `Conectar`.
2. Sube un estado de cuenta PDF desde `Carga manual`.
3. Revisa `Archivos importados`, `Detalle unificado` y `Sugerencias`.
4. Ejecuta conciliación por API:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/reconciliation/run
```

## Endpoints principales

- `POST /auth/google/start`
- `GET /auth/google/callback`
- `POST /ingest/gmail/sync`
- `POST /ingest/upload-statement`
- `GET /statements`
- `GET /movements`
- `GET /dashboard/summary`
- `POST /reconciliation/run`

También existen aliases `/api/...` usados por la UI.

## Datos locales

- Base SQLite: `storage/finance.db`
- PDFs importados: `storage/statements/`
- Tokens Gmail: `storage/tokens/`
- Cache OCR: `storage/ocr-cache/`

## Pruebas

```powershell
python -m unittest discover -s tests -v
```
