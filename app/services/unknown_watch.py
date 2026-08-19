from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from typing import Optional

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message, User

from ..config import Settings
from ..db import LocalDB
from ..google_sheets import SheetRepo

router = Router()
UTC = timezone.utc
DIYOR_SHEET_NAME = "Список Диёр"
DIYOR_TG_COLUMN_INDEX = 5


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _is_group_chat(chat_type: str) -> bool:
    return chat_type in {"group", "supergroup"}


def _mention(user_id: int, username: str, full_name: str) -> str:
    if username:
        return f"@{escape(username)}"
    display_name = escape((full_name or "").strip() or f"id {user_id}")
    return f'<a href="tg://user?id={int(user_id)}">{display_name}</a>'


async def _touch_if_relevant(db: LocalDB, chat_id: int, user: Optional[User]) -> None:
    if user is None or user.is_bot:
        return
    if not db.is_chat_approved(chat_id):
        return
    db.touch_observed_user(
        chat_id=int(chat_id),
        user_id=int(user.id),
        username=user.username or "",
        full_name=user.full_name or "",
    )


@router.message(F.chat.type.in_({"group", "supergroup"}))
async def observe_message_activity(
    message: Message,
    db: LocalDB,
):
    try:
        await _touch_if_relevant(db, message.chat.id, message.from_user)
        for member in message.new_chat_members or []:
            await _touch_if_relevant(db, message.chat.id, member)
    except Exception as e:
        print(f"unknown observe message error: {type(e).__name__}: {e}")


@router.callback_query(F.message.chat.type.in_({"group", "supergroup"}))
async def observe_callback_activity(
    cb: CallbackQuery,
    db: LocalDB,
):
    try:
        if cb.message:
            await _touch_if_relevant(db, cb.message.chat.id, cb.from_user)
    except Exception as e:
        print(f"unknown observe callback error: {type(e).__name__}: {e}")


def _alert_due(last_alerted_at: Optional[str], every_hours: int) -> bool:
    last = _parse_iso(last_alerted_at)
    if last is None:
        return True
    return datetime.now(UTC) - last >= timedelta(hours=max(1, int(every_hours)))


async def process_unknown_users_alerts(settings: Settings, repo: SheetRepo, db: LocalDB, bot) -> None:
    try:
        employees = await repo.run_async(
            repo.get_employees,
            settings.sheet_employees,
            settings.col_full_name,
            settings.col_email,
            settings.col_status,
            settings.col_telegram_id,
        )
    except Exception as e:
        print(f"unknown scan skipped: employees read failed: {type(e).__name__}: {e}")
        return

    known_ids = {int(emp.telegram_id) for emp in employees if emp.telegram_id}
    known_ids.update(int(admin_id) for admin_id in settings.admin_ids)
    try:
        known_ids.update(
            await repo.run_async(
                repo.get_tg_ids_by_column_index,
                ws_name=DIYOR_SHEET_NAME,
                col_index=DIYOR_TG_COLUMN_INDEX,
                header_row=1,
                cache_ttl_sec=settings.sheet_cache_ttl_sec,
            )
        )
        # Warm the Registration telegram_id column cache once; per-user checks below reuse it.
        await repo.run_async(
            repo.has_tg_in_sheet,
            ws_name=settings.sheet_registration,
            tg_id=0,
            col_name="telegram_id",
            header_row=1,
            cache_ttl_sec=settings.sheet_cache_ttl_sec,
        )
    except Exception as e:
        print(f"unknown scan skipped: additional sheets read failed: {type(e).__name__}: {e}")
        return

    max_users = max(1, int(settings.unknown_alert_max_users_per_chat))
    scan_limit = max_users * 10

    for chat in db.get_approved_chats():
        if not chat.is_active or not _is_group_chat(chat.chat_type or ""):
            continue

        rows = db.get_unknown_candidates(chat.chat_id, limit=scan_limit)
        unknown_rows = []

        for row in rows:
            user_id = int(row["user_id"])
            if user_id in known_ids:
                continue
            if await repo.run_async(
                repo.has_tg_in_sheet,
                ws_name=settings.sheet_registration,
                tg_id=user_id,
                col_name="telegram_id",
                header_row=1,
                cache_ttl_sec=settings.sheet_cache_ttl_sec,
            ):
                continue
            if not _alert_due(row["last_alerted_at"], settings.unknown_scan_interval_hours):
                continue
            unknown_rows.append(row)
            if len(unknown_rows) >= max_users:
                break

        if not unknown_rows:
            continue

        lines = [
            "Обнаружены пользователи, которые не прошли регистрацию в @uzum_administration_bot:",
            "",
        ]
        alerted_user_ids: list[int] = []

        for row in unknown_rows:
            user_id = int(row["user_id"])
            username = str(row["username"] or "")
            full_name = str(row["full_name"] or "")
            lines.append(f"• {_mention(user_id, username, full_name)}")
            alerted_user_ids.append(user_id)

        lines.append("")
        lines.append("Проверьте участников и при необходимости удалите их из группы.")

        try:
            await bot.send_message(
                chat_id=chat.chat_id,
                text="\n".join(lines),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            db.mark_unknown_alerted(chat.chat_id, alerted_user_ids)
        except Exception as e:
            print(f"unknown alert send failed for chat {chat.chat_id}: {type(e).__name__}: {e}")

    removed = db.purge_old_observed_users(settings.unknown_user_retention_days)
    if removed:
        print(f"unknown observations cleanup: removed={removed}")
