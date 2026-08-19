from __future__ import annotations

import asyncio
from typing import Any, Optional

from aiogram import Bot

try:
    from aiogram.exceptions import TelegramRetryAfter
except Exception:  # pragma: no cover
    TelegramRetryAfter = Exception  # type: ignore

from ..config import Settings
from ..db import LocalDB
from ..google_sheets import SheetRepo


def _norm(s: str) -> str:
    return (s or "").strip().lower()


async def _call_with_retry(fn, retries: int = 3):
    for attempt in range(retries):
        try:
            return await fn()
        except TelegramRetryAfter as e:  # type: ignore[misc]
            wait_s = int(getattr(e, "retry_after", 1) or 1) + 1
            await asyncio.sleep(wait_s)
            if attempt == retries - 1:
                raise


async def _try_kick(bot: Bot, chat_id: int, user_id: int) -> tuple[bool, str]:
    try:
        await _call_with_retry(lambda: bot.ban_chat_member(chat_id=chat_id, user_id=user_id))
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        low = err.lower()
        if "user_not_participant" in low or "not a member" in low:
            return True, "already_not_member"
        return False, err

    try:
        await _call_with_retry(lambda: bot.unban_chat_member(chat_id=chat_id, user_id=user_id))
        return True, ""
    except Exception as e:
        # Ban already succeeded => user removed.
        err = f"{type(e).__name__}: {e}"
        return True, f"unban_failed: {err}"


async def _employees(settings: Settings, repo: SheetRepo):
    return await repo.run_async(
        repo.get_employees,
        settings.sheet_employees,
        settings.col_full_name,
        settings.col_email,
        settings.col_status,
        settings.col_telegram_id,
    )


def _employees_ready_for_status_deletion(employees, delete_statuses: set[str]):
    by_tg_id: dict[int, list[Any]] = {}

    for employee in employees:
        if not employee.telegram_id:
            continue

        tg_id = int(employee.telegram_id)
        by_tg_id.setdefault(tg_id, []).append(employee)

    selected = []
    for tg_id, rows in by_tg_id.items():
        if not rows:
            continue
        if all(_norm(row.status) in delete_statuses for row in rows):
            selected.extend(rows)

    return selected


def _persist_report(
    db: LocalDB,
    report_rows: list[dict[str, Any]],
    default_reason: str,
) -> None:
    if not report_rows:
        return

    to_remove: list[tuple[int, int]] = []
    to_log: list[tuple[int, int, str, str]] = []

    for row in report_rows:
        tg_id = int(row["telegram_id"])
        chat_id = int(row["chat_id"])
        result = str(row.get("result", "error"))

        if result == "kicked":
            to_remove.append((chat_id, tg_id))

        reason = str(row.get("reason") or default_reason)

        to_log.append((tg_id, chat_id, result, reason))

    db.remove_user_from_chat_index_bulk(to_remove)
    db.log_deletions_bulk(to_log)


async def kick_user_from_indexed_chats(
    db: LocalDB,
    bot: Bot,
    telegram_id: int,
    email: str = "",
    max_kicks: Optional[int] = None,
) -> list[dict[str, Any]]:
    chats = db.get_approved_chats()
    selected = chats if max_kicks is None else chats[: max(0, int(max_kicks))]
    report: list[dict[str, Any]] = []

    for chat in selected:
        chat_id = int(chat.chat_id)
        chat_title = str(chat.title or chat.chat_id)

        ok, err = await _try_kick(bot, chat_id, int(telegram_id))

        report.append(
            {
                "email": email,
                "telegram_id": int(telegram_id),
                "username": "",
                "full_name": "",
                "chat_id": chat_id,
                "chat_title": chat_title,
                "result": "kicked" if ok else "error",
                "reason": err,
            }
        )

    return report


async def process_status_deletions(settings: Settings, repo: SheetRepo, db: LocalDB, bot: Bot) -> None:
    employees = await _employees(settings, repo)

    delete_statuses = set(settings.delete_statuses)
    admin_ids = set(settings.admin_ids)
    remaining = max(1, settings.max_kicks_per_status_run)
    processed_tg_ids: set[int] = set()

    for employee in _employees_ready_for_status_deletion(employees, delete_statuses):
        if remaining <= 0:
            break

        if not employee.telegram_id:
            continue

        tg_id = int(employee.telegram_id)
        if tg_id in admin_ids:
            continue
        if tg_id in processed_tg_ids:
            continue

        report_rows = await kick_user_from_indexed_chats(
            db=db,
            bot=bot,
            telegram_id=tg_id,
            email=employee.email,
            max_kicks=remaining,
        )
        remaining -= len(report_rows)
        processed_tg_ids.add(tg_id)
        _persist_report(db, report_rows, default_reason="status delete")


async def manual_delete_by_email(
    settings: Settings,
    repo: SheetRepo,
    db: LocalDB,
    bot: Bot,
    email: str,
) -> tuple[Optional[int], list[dict[str, Any]], str]:
    email_norm = _norm(email)

    emp = await repo.run_async(
        repo.find_employee_by_email,
        ws_name=settings.sheet_employees,
        col_full_name=settings.col_full_name,
        col_email=settings.col_email,
        col_status=settings.col_status,
        col_telegram_id=settings.col_telegram_id,
        email=email_norm,
    )

    tg_id = int(emp.telegram_id) if emp and emp.telegram_id else None

    if not tg_id:
        return None, [], "Для этой почты не найден telegram_id в листе Employees."

    report_rows = await kick_user_from_indexed_chats(
        db=db,
        bot=bot,
        telegram_id=tg_id,
        email=email_norm,
    )
    _persist_report(db, report_rows, default_reason="manual delete")

    return int(tg_id), report_rows, ""
