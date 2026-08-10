from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .config import Settings
from .db import LocalDB
from .google_sheets import SheetRepo
from .services.chat_approval import refresh_chat_activity
from .services.deletion import process_status_deletions
from .services.unknown_watch import process_unknown_users_alerts


async def purge_deletion_logs(settings: Settings, db: LocalDB) -> None:
    removed = db.purge_old_deletion_logs(settings.deletion_log_retention_days)
    if removed:
        print(f"deletion log cleanup: removed={removed}")


def setup_scheduler(settings: Settings, repo: SheetRepo, db: LocalDB, bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")

    scheduler.add_job(
        process_status_deletions,
        trigger=IntervalTrigger(minutes=settings.status_scan_interval_min),
        kwargs={"settings": settings, "repo": repo, "db": db, "bot": bot},
        id="status_deletions",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=60,
        max_instances=1,
    )

    scheduler.add_job(
        refresh_chat_activity,
        trigger=IntervalTrigger(minutes=settings.chat_healthcheck_interval_min),
        kwargs={"settings": settings, "db": db, "bot": bot},
        id="chat_healthcheck",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=60,
        max_instances=1,
    )

    scheduler.add_job(
        process_unknown_users_alerts,
        trigger=IntervalTrigger(hours=settings.unknown_scan_interval_hours),
        kwargs={"settings": settings, "repo": repo, "db": db, "bot": bot},
        id="unknown_users_alerts",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=60,
        max_instances=1,
    )

    scheduler.add_job(
        purge_deletion_logs,
        trigger=IntervalTrigger(hours=24),
        kwargs={"settings": settings, "db": db},
        id="deletion_log_cleanup",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=60,
        max_instances=1,
    )

    return scheduler
