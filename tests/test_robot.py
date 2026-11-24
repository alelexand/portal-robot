"""End-to-end: a real (headless) browser driven by the robot against the demo portal."""

import time

import pytest

from portal_robot import InvoiceRequest, LoginError, PortalRobot, ReportJob
from portal_robot.robot import DONE, FAILED, REJECTED

GOOD = InvoiceRequest("Alpha Foods Lda", "PT509876543", "FT 2026/1", "2026-10-03", "1250.00", "Goods", "test")


def test_wrong_password_stops_immediately(portal_factory, settings_for, tmp_path):
    server = portal_factory()
    with PortalRobot(settings_for(server, password="wrong"), tmp_path) as robot:
        with pytest.raises(LoginError, match="Invalid username or password"):
            robot.login()


def test_downloads_a_report(portal_factory, settings_for, tmp_path):
    server = portal_factory()
    with PortalRobot(settings_for(server), tmp_path) as robot:
        robot.login()
        result = robot.download_report(ReportJob("Beta Logistics SA", "2026-10", "Timesheet"))
    assert result.status == DONE, result.detail
    content = open(result.output, encoding="utf-8").read()
    assert content.startswith("employee_id,days_worked")
    assert result.output.endswith("2026-10_timesheet.csv")


def test_registers_a_valid_invoice(portal_factory, settings_for, tmp_path):
    server = portal_factory()
    with PortalRobot(settings_for(server), tmp_path) as robot:
        robot.login()
        result = robot.submit_invoice(GOOD)
    assert result.status == DONE and result.output.startswith("REQ-")


def test_reports_portal_validation_errors(portal_factory, settings_for, tmp_path):
    server = portal_factory()
    bad = InvoiceRequest("Alpha Foods Lda", "PT12", "FT 9", "2026-10-03", "-1", "Goods")
    with PortalRobot(settings_for(server), tmp_path) as robot:
        robot.login()
        result = robot.submit_invoice(bad)
    assert result.status == REJECTED
    assert set(result.errors) == {"supplier_vat", "amount"}
    assert result.attempts == 1 and result.screenshot


def test_retries_temporary_errors_then_gives_up(portal_factory, settings_for, tmp_path):
    server = portal_factory(flaky_rate=1.0)
    with PortalRobot(settings_for(server, retries=2), tmp_path) as robot:
        robot.login()
        result = robot.submit_invoice(GOOD)
    assert result.status == FAILED
    assert "gave up after 2 attempts" in result.detail
    assert result.screenshot.endswith(".png")


def test_signs_in_again_when_session_expires(portal_factory, settings_for, tmp_path):
    server = portal_factory(session_ttl=0.3)
    with PortalRobot(settings_for(server), tmp_path) as robot:
        robot.login()
        time.sleep(0.5)
        result = robot.submit_invoice(GOOD)
    assert result.status == DONE, result.detail


def test_unknown_option_fails_fast_without_retries(portal_factory, settings_for, tmp_path):
    server = portal_factory()
    started = time.monotonic()
    with PortalRobot(settings_for(server), tmp_path) as robot:
        robot.login()
        result = robot.download_report(ReportJob("No Such Company", "2026-10", "Timesheet"))
    assert result.status == FAILED and "not an option" in result.detail
    assert time.monotonic() - started < 6


def test_frames_are_saved_for_the_gif(portal_factory, settings_for, tmp_path):
    server = portal_factory()
    with PortalRobot(settings_for(server), tmp_path, frames_dir=tmp_path / "frames") as robot:
        robot.login()
    assert len(list((tmp_path / "frames").glob("*.png"))) == 2
