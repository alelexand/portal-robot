"""The demo portal itself, tested with Flask's test client (no browser)."""

import re
import time
from datetime import date, timedelta

import pytest

from demo_portal import create_app
from demo_portal.app import validate_request

VALID = dict(company="C01", supplier_vat="PT509876543", invoice_number="FT 1", invoice_date="2026-10-01",
             amount="10.50", category="Goods")


def _csrf(html: bytes) -> str:
    return re.search(rb'name="csrf_token" value="([^"]+)"', html).group(1).decode()


@pytest.fixture
def client():
    app = create_app(report_delay=(0, 0))
    return app.test_client()


def _login(client, password="demo123"):
    token = _csrf(client.get("/login").data)
    return client.post("/login", data={"username": "demo", "password": password, "csrf_token": token})


def test_pages_require_login(client):
    r = client.get("/requests")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_wrong_password(client):
    r = _login(client, password="nope")
    assert r.status_code == 401 and b"Invalid username or password" in r.data


def test_login_and_dashboard(client):
    assert _login(client).status_code == 302
    assert b"Welcome" in client.get("/").data


def test_form_without_csrf_token_is_refused(client):
    assert client.post("/login", data={"username": "demo", "password": "demo123"}).status_code == 400


def test_api_answers_401_after_session_expires():
    client = create_app(session_ttl=0).test_client()
    _login(client)
    time.sleep(0.01)
    assert client.get("/api/reports/whatever").status_code == 401


def test_requests_are_paginated(client):
    _login(client)
    html = client.get("/requests?page=3").data
    assert b"Page 3 of 3" in html and b"23 requests" in html


@pytest.mark.parametrize("field, value, message", [
    ("company", "C99", "Choose a company"),
    ("supplier_vat", "PT12345", "EU VAT number"),
    ("supplier_vat", "XX123456789", "EU VAT number"),
    ("invoice_number", "", "required"),
    ("invoice_date", "2026-02-30", "valid date"),
    ("invoice_date", (date.today() + timedelta(days=2)).isoformat(), "future"),
    ("amount", "-5", "between"),
    ("amount", "abc", "between"),
    ("category", "Food", "Choose a category"),
])
def test_validation(field, value, message):
    _, errors = validate_request({**VALID, field: value}, [])
    assert message in errors[field]


def test_valid_request_is_normalised():
    clean, errors = validate_request({**VALID, "supplier_vat": "pt 509 876 543", "amount": "1250,5"}, [])
    assert errors == {} and clean["supplier_vat"] == "PT509876543" and clean["amount"] == "1250.50"


def test_duplicate_invoice_is_refused():
    clean, _ = validate_request(VALID, [])
    _, errors = validate_request({**VALID, "invoice_number": "ft 1"}, [clean])
    assert "already registered" in errors["invoice_number"]
