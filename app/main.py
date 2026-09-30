import hashlib
import hmac
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import psycopg
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from psycopg.rows import dict_row
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
load_dotenv(Path(__file__).parent.parent / ".env")

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://helpdesk:helpdesk@localhost:5432/helpdesk"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id           SERIAL PRIMARY KEY,
    subject      TEXT NOT NULL,
    body         TEXT NOT NULL,
    sender_email TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

log = logging.getLogger("uvicorn.error")  # uvicorn shows INFO for this logger
PAKISTAN_TZ = ZoneInfo("Asia/Karachi")


def get_conn():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


@asynccontextmanager
async def lifespan(app: FastAPI):
    with get_conn() as conn:
        conn.execute(SCHEMA)
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


# --- AI triage hook (behind a flag, off by default) ---------------------------

def ai_triage_enabled() -> bool:
    return os.environ.get("AI_TRIAGE_ENABLED", "false").lower() == "true"


def notify_triage(ticket: dict) -> None:
    if not ai_triage_enabled():
        log.info("ai triage skipped, flag off")
        return
    url = os.environ.get("TRIAGE_URL")
    secret = os.environ.get("TRIAGE_WEBHOOK_SECRET")
    if not url or not secret:
        log.error("ai triage enabled but TRIAGE_URL or TRIAGE_WEBHOOK_SECRET is missing")
        return
    body = json.dumps(ticket, default=str).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    try:
        response = httpx.post(
            url,
            content=body,
            headers={"X-Signature": sig, "Content-Type": "application/json"},
            timeout=5,
        )
        response.raise_for_status()
    except Exception:
        log.exception("ai triage call failed")  # never break ticket submission


# --- Routes --------------------------------------------------------------------

@app.get("/")
def submit_page(request: Request):
    return templates.TemplateResponse(request, "submit.html")


@app.post("/tickets")
def create_ticket(
    background_tasks: BackgroundTasks,
    subject: str = Form(...),
    body: str = Form(...),
    sender_email: str = Form(...),
):
    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO tickets (subject, body, sender_email) VALUES (%s, %s, %s) "
            "RETURNING id, created_at",
            (subject, body, sender_email),
        ).fetchone()
    background_tasks.add_task(
        notify_triage,
        {
            "source_id": f"helpdesk-{row['id']}",
            "subject": subject,
            "body": body,
            "sender_email": sender_email,
            "created_at": row["created_at"],
        },
    )
    return RedirectResponse("/tickets", status_code=303)


@app.get("/tickets")
def list_tickets(request: Request):
    with get_conn() as conn:
        tickets = conn.execute(
            "SELECT id, subject, sender_email, status, created_at "
            "FROM tickets ORDER BY id DESC"
        ).fetchall()
    for ticket in tickets:
        ticket["created_at"] = ticket["created_at"].astimezone(PAKISTAN_TZ)
    return templates.TemplateResponse(request, "list.html", {"tickets": tickets})

@app.post("/tickets/{ticket_id}/status")
async def update_ticket_status(ticket_id: int, request: Request):
    raw = await request.body()
    secret = os.environ.get("TRIAGE_WEBHOOK_SECRET", "")
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, request.headers.get("X-Signature", "")):
        raise HTTPException(status_code=401, detail="bad signature")
    try:
        payload = json.loads(raw)
        new_status = payload["status"]
    except (ValueError, KeyError):
        raise HTTPException(status_code=422, detail="invalid payload")
    with get_conn() as conn:
        conn.execute("UPDATE tickets SET status=%s WHERE id=%s", (new_status, ticket_id))
    return {"status": "updated"}