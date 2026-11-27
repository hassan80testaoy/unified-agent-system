"""APScheduler entrypoint for daily 08:00 reports and repeating tasks."""

from __future__ import annotations

import logging
import time

from apscheduler.schedulers.background import BackgroundScheduler

from app.db import SessionLocal
from app.models.core import Workspace
from app.services.reports import generate_report

logger = logging.getLogger(__name__)


def _daily_report_job() -> None:
    db = SessionLocal()
    try:
        for ws in db.query(Workspace).all():
            generate_report(db, workspace_id=ws.id, period="daily", skip_if_empty=True)
    finally:
        db.close()


def main() -> None:
    scheduler = BackgroundScheduler(timezone="Africa/Cairo")
    scheduler.add_job(_daily_report_job, "cron", hour=8, minute=0)
    scheduler.start()
    logger.info("Unified Agent Scheduler started (08:00 Africa/Cairo daily report).")
    try:
        while True:
            time.sleep(30)
    except KeyboardInterrupt:
        scheduler.shutdown()


if __name__ == "__main__":
    main()
