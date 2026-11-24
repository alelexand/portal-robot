"""A deliberately old-fashioned supplier portal for the robot to work against.

It behaves like the portals robots usually face in real life: a login with a CSRF token,
sessions that expire, reports that take a few seconds to be generated, server-side form
validation, paginated tables and, optionally, random "503 Service unavailable" errors.
Everything is in memory and every company and number is fictional.
"""

from __future__ import annotations

import csv
import io
import logging
import random
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import Flask, Response, abort, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.serving import make_server

COMPANIES = {
    "C01": "Alpha Foods Lda",
    "C02": "Beta Logistics SA",
    "C03": "Gamma Retail GmbH",
    "C04": "Delta Services BV",
    "C05": "Epsilon Tech SRL",
}
REPORT_TYPES = {"payroll": "Payroll summary", "timesheet": "Timesheet", "invoices": "Supplier invoices"}
CATEGORIES = ["Goods", "Services", "Transport", "Utilities", "Other"]
VAT_PATTERN = re.compile(r"^(AT|BE|BG|CY|CZ|DE|DK|EE|EL|ES|FI|FR|HR|HU|IE|IT|LT|LU|LV|MT|NL|PL|PT|RO|SE|SI|SK)[0-9A-Z]{8,12}$")
PAGE_SIZE = 10


@dataclass
class ReportJob:
    id: str
    company: str
    month: str
    kind: str
    ready_at: float


@dataclass
class PortalState:
    requests: list[dict] = field(default_factory=list)
    jobs: dict[str, ReportJob] = field(default_factory=dict)
    sequence: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def next_protocol(self) -> str:
        with self.lock:
            self.sequence += 1
            return f"REQ-{date.today().year}-{self.sequence:05d}"


def _seed_requests(state: PortalState, rng: random.Random) -> None:
    for _ in range(23):
        company = rng.choice(list(COMPANIES))
        state.requests.append({
            "protocol": state.next_protocol(),
            "company": company,
            "supplier_vat": f"PT{rng.randint(500000000, 599999999)}",
            "invoice_number": f"FT {rng.randint(2026, 2026)}/{rng.randint(1, 999)}",
            "invoice_date": date(2026, rng.randint(1, 10), rng.randint(1, 28)).isoformat(),
            "amount": f"{Decimal(rng.randint(1000, 500000)) / 100:.2f}",
            "category": rng.choice(CATEGORIES),
            "notes": "",
            "created_at": datetime(2026, 10, rng.randint(1, 31), rng.randint(8, 18), rng.randint(0, 59)),
        })


def _report_csv(job: ReportJob) -> str:
    rng = random.Random(f"{job.company}-{job.month}-{job.kind}")
    out = io.StringIO()
    w = csv.writer(out)
    if job.kind == "payroll":
        w.writerow(["employee_id", "gross", "deductions", "net"])
        for i in range(1, rng.randint(6, 12)):
            gross = rng.randint(150000, 400000) / 100
            ded = round(gross * 0.26, 2)
            w.writerow([f"E{i:03d}", f"{gross:.2f}", f"{ded:.2f}", f"{gross - ded:.2f}"])
    elif job.kind == "timesheet":
        w.writerow(["employee_id", "days_worked", "hours_worked", "overtime_hours"])
        for i in range(1, rng.randint(6, 12)):
            w.writerow([f"E{i:03d}", rng.randint(18, 22), rng.randint(140, 176), rng.randint(0, 12)])
    else:
        w.writerow(["supplier_vat", "invoice_number", "amount"])
        for i in range(1, rng.randint(5, 10)):
            w.writerow([f"PT{rng.randint(500000000, 599999999)}", f"FT {job.month[:4]}/{i}", f"{rng.randint(5000, 90000) / 100:.2f}"])
    return out.getvalue()


def validate_request(form: dict, existing: list[dict]) -> tuple[dict, dict[str, str]]:
    """Return (clean values, errors by field)."""
    errors: dict[str, str] = {}
    clean = {k: (form.get(k) or "").strip() for k in
             ("company", "supplier_vat", "invoice_number", "invoice_date", "amount", "category", "notes")}
    clean["supplier_vat"] = clean["supplier_vat"].upper().replace(" ", "")

    if clean["company"] not in COMPANIES:
        errors["company"] = "Choose a company."
    if not VAT_PATTERN.match(clean["supplier_vat"]):
        errors["supplier_vat"] = "Enter an EU VAT number, e.g. PT123456789."
    if not clean["invoice_number"]:
        errors["invoice_number"] = "Invoice number is required."
    elif any(r["company"] == clean["company"] and r["supplier_vat"] == clean["supplier_vat"]
             and r["invoice_number"].lower() == clean["invoice_number"].lower() for r in existing):
        errors["invoice_number"] = "This invoice was already registered."
    try:
        invoice_date = date.fromisoformat(clean["invoice_date"])
        if invoice_date > date.today():
            errors["invoice_date"] = "Invoice date cannot be in the future."
    except ValueError:
        errors["invoice_date"] = "Enter a valid date."
    try:
        amount = Decimal(clean["amount"].replace(",", "."))
        if amount <= 0 or amount > 1_000_000:
            raise InvalidOperation
        clean["amount"] = f"{amount:.2f}"
    except InvalidOperation:
        errors["amount"] = "Enter an amount between 0.01 and 1,000,000."
    if clean["category"] not in CATEGORIES:
        errors["category"] = "Choose a category."
    return clean, errors


def create_app(
    username: str = "demo",
    password: str = "demo123",
    report_delay: tuple[float, float] = (0.8, 2.0),
    flaky_rate: float = 0.0,
    session_ttl: float = 15 * 60,
    seed: int = 7,
) -> Flask:
    app = Flask(__name__)
    app.secret_key = secrets.token_hex(16)
    state = PortalState()
    rng = random.Random(seed)
    _seed_requests(state, random.Random(seed))
    app.config.update(PORTAL_STATE=state, FLAKY_RATE=flaky_rate)

    def csrf_token() -> str:
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(16)
        return session["csrf"]

    app.jinja_env.globals.update(csrf_token=csrf_token, companies=COMPANIES)

    def check_csrf() -> None:
        expected, sent = session.get("csrf"), request.form.get("csrf_token")
        if not expected or not sent or not secrets.compare_digest(expected, sent):
            abort(400, "Invalid form token. Reload the page and try again.")

    def maybe_fail():
        if rng.random() < app.config["FLAKY_RATE"]:
            return render_template("error.html", code=503,
                                   message="Service temporarily unavailable. Please try again in a moment."), 503
        return None

    def login_required(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            started = session.get("login_at")
            if not started or time.time() - started > session_ttl:
                session.pop("login_at", None)
                if request.path.startswith("/api/"):
                    return jsonify(error="session expired"), 401
                if started:
                    flash("Your session has expired. Please sign in again.", "warning")
                return redirect(url_for("login", next=request.full_path))
            return view(*args, **kwargs)
        return wrapper

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            check_csrf()
            if request.form.get("username") == username and request.form.get("password") == password:
                session["login_at"] = time.time()
                target = request.args.get("next") or url_for("dashboard")
                return redirect(target if target.startswith("/") else url_for("dashboard"))
            error = "Invalid username or password."
        return render_template("login.html", error=error), (401 if error else 200)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @login_required
    def dashboard():
        return render_template("dashboard.html", total=len(state.requests))

    @app.route("/reports", methods=["GET", "POST"])
    @login_required
    def reports():
        if request.method == "POST":
            check_csrf()
            if (failure := maybe_fail()) is not None:
                return failure
            company, month, kind = (request.form.get(k, "") for k in ("company", "month", "report_type"))
            if company not in COMPANIES or kind not in REPORT_TYPES or not re.fullmatch(r"\d{4}-\d{2}", month):
                flash("Choose a company, a month and a report type.", "error")
                return redirect(url_for("reports"))
            job = ReportJob(secrets.token_hex(6), company, month, kind, time.time() + rng.uniform(*report_delay))
            state.jobs[job.id] = job
            return redirect(url_for("report_status_page", job_id=job.id))
        return render_template("reports.html", report_types=REPORT_TYPES)

    @app.get("/reports/<job_id>")
    @login_required
    def report_status_page(job_id):
        job = state.jobs.get(job_id) or abort(404)
        return render_template("report_status.html", job=job, company=COMPANIES[job.company],
                               kind=REPORT_TYPES[job.kind])

    @app.get("/api/reports/<job_id>")
    @login_required
    def report_status(job_id):
        job = state.jobs.get(job_id) or abort(404)
        return jsonify(ready=time.time() >= job.ready_at)

    @app.get("/reports/<job_id>/download")
    @login_required
    def report_download(job_id):
        job = state.jobs.get(job_id) or abort(404)
        if time.time() < job.ready_at:
            abort(409, "Report is not ready yet.")
        filename = f"{job.company}_{job.month}_{job.kind}.csv"
        return Response(_report_csv(job), mimetype="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.get("/requests")
    @login_required
    def requests_list():
        q = request.args.get("q", "").strip().lower()
        rows = sorted(state.requests, key=lambda r: r["created_at"], reverse=True)
        if q:
            rows = [r for r in rows if q in " ".join(str(v) for v in r.values()).lower()]
        page = max(1, request.args.get("page", 1, type=int))
        pages = max(1, -(-len(rows) // PAGE_SIZE))
        page = min(page, pages)
        return render_template("requests_list.html", rows=rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE],
                               page=page, pages=pages, q=q, total=len(rows))

    @app.route("/requests/new", methods=["GET", "POST"])
    @login_required
    def request_new():
        values, errors = {}, {}
        if request.method == "POST":
            check_csrf()
            if (failure := maybe_fail()) is not None:
                return failure
            values, errors = validate_request(request.form, state.requests)
            if not errors:
                record = {**values, "protocol": state.next_protocol(), "created_at": datetime.now()}
                state.requests.append(record)
                return redirect(url_for("request_detail", protocol=record["protocol"]))
        return render_template("request_form.html", values=values, errors=errors, categories=CATEGORIES), \
            (422 if errors else 200)

    @app.get("/requests/<protocol>")
    @login_required
    def request_detail(protocol):
        record = next((r for r in state.requests if r["protocol"] == protocol), None) or abort(404)
        return render_template("request_detail.html", r=record)

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(409)
    def http_error(err):
        return render_template("error.html", code=err.code, message=err.description), err.code

    return app


class PortalServer:
    """Runs the portal in a background thread, e.g. for tests and the one-command demo."""

    def __init__(self, app: Flask, host: str = "127.0.0.1", port: int = 0, quiet: bool = True):
        if quiet:
            logging.getLogger("werkzeug").setLevel(logging.ERROR)
        self._server = make_server(host, port, app, threaded=True)
        self.url = f"http://{host}:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "PortalServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)
