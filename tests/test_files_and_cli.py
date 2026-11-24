from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from portal_robot import Settings, TaskResult
from portal_robot.cli import main
from portal_robot.files import read_invoices, read_report_jobs, write_summary
from tests.conftest import CHANNEL

SAMPLE = Path(__file__).resolve().parent.parent / "sample_data"


def test_reads_sample_files():
    assert len(read_report_jobs(SAMPLE / "reports.csv")) == 4
    invoices = read_invoices(SAMPLE / "invoices.csv")
    assert len(invoices) == 8 and invoices[1].notes == ""


def test_reads_excel_and_semicolons(tmp_path):
    wb = Workbook()
    wb.active.append(["Company", "Month", "Report type"])
    wb.active.append(["Alpha Foods Lda", "2026-10", "Timesheet"])
    wb.save(tmp_path / "r.xlsx")
    assert read_report_jobs(tmp_path / "r.xlsx")[0].report_type == "Timesheet"

    (tmp_path / "r.csv").write_text("company;month;report_type\nAlpha Foods Lda;2026-10;Timesheet\n", encoding="utf-8")
    assert read_report_jobs(tmp_path / "r.csv")[0].company == "Alpha Foods Lda"


def test_missing_column(tmp_path):
    (tmp_path / "r.csv").write_text("company,month\nA,2026-10\n", encoding="utf-8")
    with pytest.raises(ValueError, match="report_type"):
        read_report_jobs(tmp_path / "r.csv")


def test_summary_workbook(tmp_path):
    path = write_summary([TaskResult("invoice", "A · 1", "done", "Registered", "REQ-1", 1)], tmp_path / "s.xlsx")
    ws = load_workbook(path).active
    assert ws["C2"].value == "DONE" and ws["D2"].value == "REQ-1"


def test_settings_never_print_the_password():
    assert "secret" not in repr(Settings("http://x", "user", "secret"))


def test_settings_from_env_requires_credentials(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for key in ("PORTAL_URL", "PORTAL_USER", "PORTAL_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError, match="PORTAL_PASSWORD"):
        Settings.from_env()


def test_demo_command_end_to_end(tmp_path, capsys):
    args = ["demo", "--flaky", "0", "--out", str(tmp_path)]
    if CHANNEL:
        args += ["--browser-channel", CHANNEL]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "9 done, 3 rejected by the portal, 0 failed" in out
    assert (tmp_path / "summary.xlsx").exists() and (tmp_path / "run.log").exists()
    assert len(list((tmp_path / "downloads").rglob("*.csv"))) == 4
