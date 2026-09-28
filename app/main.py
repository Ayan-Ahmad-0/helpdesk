import os
from contextlib import asynccontextmanager

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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


@app.get("/")
def submit_page(request: Request):
    return templates.TemplateResponse(request, "submit.html")


@app.post("/tickets")
def create_ticket(
    subject: str = Form(...),
    body: str = Form(...),
    sender_email: str = Form(...),
):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO tickets (subject, body, sender_email) VALUES (%s, %s, %s)",
            (subject, body, sender_email),
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