"""Reads the robot's to-do lists (CSV or Excel) and writes the run summary (Excel)."""

from __future__ import annotations

import csv
from dataclasses import fields
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from .robot import DONE, FAILED, REJECTED, InvoiceRequest, ReportJob, TaskResult


def read_rows(path: str | Path) -> list[dict[str, str]]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if path.suffix.lower() == ".xlsx":
        ws = load_workbook(path, read_only=True, data_only=True).active
        rows = list(ws.iter_rows(values_only=True))
        header = [str(h).strip().lower().replace(" ", "_") for h in rows[0]]
        data = [dict(zip(header, ("" if v is None else str(v).strip() for v in r))) for r in rows[1:]]
    elif path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as f:
            sample = f.read(2048)
            f.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=",;")
            data = [{k.strip().lower().replace(" ", "_"): (v or "").strip() for k, v in row.items()}
                    for row in csv.DictReader(f, dialect=dialect)]
    else:
        raise ValueError(f"Unsupported file type: {path.suffix} (use .csv or .xlsx)")
    return [r for r in data if any(r.values())]


def _build(cls, path: str | Path):
    rows = read_rows(path)
    required = [f.name for f in fields(cls) if f.name != "notes"]  # notes is the only optional column
    missing = [c for c in required if rows and c not in rows[0]]
    if missing:
        raise ValueError(f"{Path(path).name}: missing column(s) {', '.join(missing)}")
    names = {f.name for f in fields(cls)}
    return [cls(**{k: v for k, v in row.items() if k in names}) for row in rows]


def read_report_jobs(path: str | Path) -> list[ReportJob]:
    return _build(ReportJob, path)


def read_invoices(path: str | Path) -> list[InvoiceRequest]:
    return _build(InvoiceRequest, path)


FILLS = {DONE: "DCFCE7", REJECTED: "FEF3C7", FAILED: "FDE2E1"}


def write_summary(results: list[TaskResult], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Run summary"
    headers = ["Task", "Item", "Status", "Result", "Details", "Attempts", "Seconds", "Screenshot"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1E3A8A")
    for r in results:
        ws.append([r.task, r.key, r.status.upper(), r.output, r.detail, r.attempts, round(r.seconds, 1), r.screenshot])
        ws.cell(ws.max_row, 3).fill = PatternFill("solid", fgColor=FILLS[r.status])
    for i, width in enumerate([10, 42, 11, 48, 60, 9, 9, 50], start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    wb.save(path)
    return path
