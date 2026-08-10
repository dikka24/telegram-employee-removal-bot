import asyncio

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from .config import load_settings
from .db import LocalDB
from .google_sheets import SheetRepo
from .scheduler import setup_scheduler
from .services.admin import router as admin_router
from .services.chat_approval import router as chat_approval_router
from .services.registration import router as registration_router
from .services.unknown_watch import router as unknown_watch_router


async def main():
    settings = load_settings()

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher(storage=MemoryStorage())

    repo = SheetRepo.from_service_account(settings.google_creds_path, settings.google_sheet_id)
    db = LocalDB(settings.db_path)
    db.init_schema()
    removed_logs = db.purge_old_deletion_logs(settings.deletion_log_retention_days)
    if removed_logs:
        print(f"deletion log cleanup: removed={removed_logs}")

    dp["settings"] = settings
    dp["repo"] = repo
    dp["db"] = db

    dp.include_router(registration_router)
    dp.include_router(admin_router)
    dp.include_router(chat_approval_router)
    dp.include_router(unknown_watch_router)

    scheduler = setup_scheduler(settings, repo, db, bot)
    scheduler.start()

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
