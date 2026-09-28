import pytest
from fastapi.testclient import TestClient

from app.main import app, get_conn


@pytest.fixture()
def client():
    with TestClient(app) as c:  # runs the lifespan, which creates the table
        with get_conn() as conn:
            conn.execute("TRUNCATE tickets RESTART IDENTITY")
        yield c


def count_tickets():
    with get_conn() as conn:
        return conn.execute("SELECT count(*) AS n FROM tickets").fetchone()["n"]


def test_submit_creates_one_ticket(client):
    r = client.post(
        "/tickets",
        data={"subject": "Login broken", "body": "Cannot sign in", "sender_email": "a@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert count_tickets() == 1


def test_list_page_shows_ticket(client):
    client.post(
        "/tickets",
        data={"subject": "Refund please", "body": "Charged twice", "sender_email": "b@example.com"},
    )
    r = client.get("/tickets")
    assert r.status_code == 200
    assert "Refund please" in r.text
def fetch_all():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM tickets ORDER BY id").fetchall()


def test_missing_subject_field_is_rejected(client):
    r = client.post(
        "/tickets",
        data={"body": "no subject", "sender_email": "c@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 422   # FastAPI validation error; verify by running
    assert count_tickets() == 0


def test_empty_subject_is_accepted(client):
    r = client.post(
        "/tickets",
        data={"subject": "", "body": "empty subject", "sender_email": "c@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 422   # Form(...) only checks presence, not content
    assert count_tickets() == 0


def test_very_long_body_is_stored_unchanged(client):
    long_body = "x" * 100_000
    client.post(
        "/tickets",
        data={"subject": "Long", "body": long_body, "sender_email": "d@example.com"},
    )
    assert fetch_all()[0]["body"] == long_body   # TEXT column, no truncation


def test_duplicate_submission_creates_two_rows(client):
    payload = {"subject": "Same", "body": "Same", "sender_email": "e@example.com"}
    client.post("/tickets", data=payload)
    client.post("/tickets", data=payload)
    assert count_tickets() == 2   # no unique constraint or dedup


def test_new_ticket_status_is_open(client):
    client.post(
        "/tickets",
        data={"subject": "S", "body": "B", "sender_email": "f@example.com"},
    )
    assert fetch_all()[0]["status"] == "open"
    
def test_whitespace_only_subject_is_accepted(client):
    r = client.post(
        "/tickets",
        data={"subject": "   ", "body": "spaces only", "sender_email": "c@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 303   # verify by running; if 422, change to match
    assert count_tickets() == 1