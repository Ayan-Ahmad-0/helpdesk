import hashlib
import hmac
import json
import logging
import os
from contextlib import asynccontextmanager

import httpx
import psycopg
from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from psycopg.rows import dict_row

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


def get_conn():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


@asynccontextmanager
async def lifespan(app: FastAPI):
    with get_conn() as conn:
        conn.execute(SCHEMA)
    yield


app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="app/templates")


# --- AI triage hook (behind a flag, off by default) ---------------------------

def ai_triage_enabled() -> bool:
    return os.environ.get("AI_TRIAGE_ENABLED", "false").lower() == "true"


def notify_triage(ticket: dict) -> None:
    if not ai_triage_enabled():
        log.info("ai triage skipped, flag off")
        return
    body = json.dumps(ticket, default=str).encode()
    secret = os.environ["TRIAGE_WEBHOOK_SECRET"].encode()
    sig = hmac.new(secret, body, hashlib.sha256).hexdigest()
    try:
        httpx.post(
            os.environ["TRIAGE_URL"],
            content=body,
            headers={"X-Signature": sig, "Content-Type": "application/json"},
            timeout=5,
        )
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
    return templates.TemplateResponse(request, "list.html", {"tickets": tickets})