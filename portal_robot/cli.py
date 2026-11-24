"""Command line.

  python -m portal_robot demo                 start the demo portal and run the robot against it
  python -m portal_robot serve                start only the demo portal (http://127.0.0.1:5000)
  python -m portal_robot run --reports ... --invoices ...   run against the portal set in .env
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import replace
from pathlib import Path

from .config import Settings
from .files import read_invoices, read_report_jobs, write_summary
from .robot import DONE, FAILED, REJECTED, LoginError, PortalRobot, TaskResult

log = logging.getLogger("portal_robot")
SAMPLE = Path(__file__).resolve().parent.parent / "sample_data"


def _setup_logging(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S")
    root = logging.getLogger("portal_robot")
    root.handlers.clear()
    root.setLevel(logging.INFO)
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(out_dir / "run.log", encoding="utf-8")):
        handler.setFormatter(fmt)
        root.addHandler(handler)


def execute(settings: Settings, reports: Path | None, invoices: Path | None, out_dir: Path,
            frames_dir: Path | None = None) -> list[TaskResult]:
    jobs = read_report_jobs(reports) if reports else []
    requests = read_invoices(invoices) if invoices else []
    log.info("Starting: %d report(s) to download, %d invoice(s) to register", len(jobs), len(requests))
    results: list[TaskResult] = []
    with PortalRobot(settings, out_dir, frames_dir) as robot:
        robot.login()
        for job in jobs:
            results.append(robot.download_report(job))
        for req in requests:
            results.append(robot.submit_invoice(req))
    summary = write_summary(results, out_dir / "summary.xlsx")
    _print_summary(results, summary)
    return results


def _print_summary(results: list[TaskResult], summary: Path) -> None:
    icons = {DONE: "OK ", REJECTED: "REJ", FAILED: "ERR"}
    print()
    for r in results:
        print(f"  [{icons[r.status]}] {r.task:<8} {r.key:<46} {r.output or r.detail}"[:160])
    counts = {s: sum(1 for r in results if r.status == s) for s in (DONE, REJECTED, FAILED)}
    print(f"\n  {counts[DONE]} done, {counts[REJECTED]} rejected by the portal, {counts[FAILED]} failed")
    print(f"  Summary: {summary}")


def cmd_run(args: argparse.Namespace) -> int:
    out = Path(args.out or f"runs/{time.strftime('%Y%m%d-%H%M%S')}")
    _setup_logging(out)
    settings = Settings.from_env()
    if args.headed:
        settings = replace(settings, headless=False)
    results = execute(settings, args.reports, args.invoices, out)
    return 1 if any(r.status == FAILED for r in results) else 0


def cmd_serve(args: argparse.Namespace) -> int:
    from demo_portal import create_app
    print(f"Demo portal on http://127.0.0.1:{args.port}  (user: demo, password: demo123)")
    create_app(flaky_rate=args.flaky).run(port=args.port)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    from demo_portal import PortalServer, create_app

    out = Path(args.out or f"runs/demo-{time.strftime('%Y%m%d-%H%M%S')}")
    _setup_logging(out)
    app = create_app(flaky_rate=args.flaky, session_ttl=args.session_ttl, seed=args.seed)
    with PortalServer(app) as server:
        log.info("Demo portal running at %s (flaky rate %.0f%%, session %ss)", server.url, args.flaky * 100, args.session_ttl)
        settings = Settings(base_url=server.url, username="demo", password="demo123", headless=not args.headed,
                            browser_channel=args.browser_channel, slow_mo_ms=args.slow_mo)
        results = execute(settings, SAMPLE / "reports.csv", SAMPLE / "invoices.csv", out,
                          Path(args.frames) if args.frames else None)
    return 1 if any(r.status == FAILED for r in results) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="portal_robot", description="Web robot that downloads reports and fills forms.")
    sub = parser.add_subparsers(dest="command", required=True)

    d = sub.add_parser("demo", help="run the robot against the bundled demo portal")
    d.add_argument("--headed", action="store_true", help="show the browser window")
    d.add_argument("--slow-mo", type=int, default=0, help="milliseconds between actions, to watch it work")
    d.add_argument("--flaky", type=float, default=0.25, help="share of form posts that fail with 503")
    d.add_argument("--session-ttl", type=float, default=900, help="session lifetime in seconds")
    d.add_argument("--seed", type=int, default=7)
    d.add_argument("--browser-channel", default="", help='"msedge" or "chrome" to use an installed browser')
    d.add_argument("--frames", help="save a screenshot of every step to this folder")
    d.add_argument("--out")
    d.set_defaults(func=cmd_demo)

    s = sub.add_parser("serve", help="start only the demo portal")
    s.add_argument("--port", type=int, default=5000)
    s.add_argument("--flaky", type=float, default=0.0)
    s.set_defaults(func=cmd_serve)

    r = sub.add_parser("run", help="run against the portal configured in .env")
    r.add_argument("--reports", type=Path, help="CSV/Excel with company, month, report_type")
    r.add_argument("--invoices", type=Path, help="CSV/Excel with the invoices to register")
    r.add_argument("--headed", action="store_true")
    r.add_argument("--out")
    r.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError, LoginError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
