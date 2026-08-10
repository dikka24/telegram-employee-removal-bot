from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ..config import Settings
from ..db import LocalDB
from ..google_sheets import SheetRepo
from ..utils.excel_export import make_xlsx
from ..utils.validators import is_valid_email, normalize_email
from .deletion import manual_delete_by_email

router = Router()


class AdminState(StatesGroup):
    waiting_manual_email = State()


def is_admin(user_id: int, settings: Settings) -> bool:
    return user_id in settings.admin_ids


def status_text() -> str:
    return "- Функционал по умолчанию\n" \
    "Если сотрудник уволен, то автоматически бот удаляет с групп"


def admin_panel_text() -> str:
    return (
        "Функционал админ-панели:\n\n"
        "- Ручное удаление по почте\n"
        "Нужно ввести корпоративную почту сотрудника, после чего бот удалит его из всех групп, где он присутствует.\n\n"
        "- Список групп\n"
        "Показывает названия групп, в которых работает бот-удалятор."
    )


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Ручное удаление по почте", callback_data="admin:manual_email")],
            [InlineKeyboardButton(text="📋 Список групп", callback_data="admin:chats")],
        ]
    )


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:menu")]]
    )


async def send_admin_menu(message: Message):
    await message.answer(
        f"{admin_panel_text()}\n\n{status_text()}",
        reply_markup=admin_menu_kb(),
    )


@router.message(Command("admin"))
async def admin_cmd(message: Message, settings: Settings):
    if not is_admin(message.from_user.id, settings):
        return
    await send_admin_menu(message)


@router.message(F.text.casefold() == "админ")
async def admin_text(message: Message, settings: Settings):
    if not is_admin(message.from_user.id, settings):
        return
    await send_admin_menu(message)


@router.callback_query(F.data == "admin:menu")
async def admin_menu_cb(cb: CallbackQuery, settings: Settings, state: FSMContext):
    if not is_admin(cb.from_user.id, settings):
        return await cb.answer("Нет доступа", show_alert=True)

    await state.clear()
    await cb.message.edit_text(
        f"{admin_panel_text()}\n\n{status_text()}",
        reply_markup=admin_menu_kb(),
    )
    await cb.answer()


@router.callback_query(F.data == "admin:manual_email")
async def admin_manual_email_start(cb: CallbackQuery, settings: Settings, state: FSMContext):
    if not is_admin(cb.from_user.id, settings):
        return await cb.answer("Нет доступа", show_alert=True)

    await state.clear()
    await state.set_state(AdminState.waiting_manual_email)
    await cb.message.edit_text(
        "Введите корпоративную почту сотрудника для ручного удаления.\n"
        "Или нажмите «Назад».",
        reply_markup=back_kb(),
    )
    await cb.answer()


@router.message(AdminState.waiting_manual_email, F.text)
async def admin_manual_email_input(
    message: Message,
    settings: Settings,
    state: FSMContext,
    repo: SheetRepo,
    db: LocalDB,
    bot,
):
    if not is_admin(message.from_user.id, settings):
        await state.clear()
        return

    email = normalize_email(message.text or "")
    if not is_valid_email(email):
        await message.answer("Некорректный email. Введите корректную почту:")
        return

    tg_id, report_rows, err = await manual_delete_by_email(
        settings=settings,
        repo=repo,
        db=db,
        bot=bot,
        email=email,
    )

    await state.clear()

    if err:
        await message.answer(err, reply_markup=back_kb())
        return

    kicked_rows = [r for r in report_rows if str(r.get("result")) == "kicked"]
    error_rows = [r for r in report_rows if str(r.get("result")) != "kicked"]

    if kicked_rows or error_rows:
        xlsx_rows = []
        for row in report_rows:
            xlsx_rows.append(
                [
                    str(row.get("email", "")),
                    str(row.get("telegram_id", "")),
                    str(row.get("username", "")),
                    str(row.get("full_name", "")),
                    str(row.get("chat_title", "")),
                    str(row.get("chat_id", "")),
                    str(row.get("result", "")),
                    str(row.get("reason", "")),
                ]
            )

        xlsx = make_xlsx(
            headers=["email", "telegram_id", "username", "full_name", "chat_title", "chat_id", "result", "reason"],
            rows=xlsx_rows,
        )
        await bot.send_document(
            message.chat.id,
            BufferedInputFile(xlsx.getvalue(), filename="manual_delete_report.xlsx"),
        )

    if not report_rows:
        await message.answer(
            f"TG ID найден ({tg_id}), но пользователь не найден в управляемых группах. Ничего не удалено.",
            reply_markup=back_kb(),
        )
        return

    await message.answer(
        "Процесс ручного удаления завершён ✅\n"
        f"Почта: {email}\n"
        f"Telegram ID: {tg_id}\n"
        f"Кикнуто: {len(kicked_rows)}\n"
        f"Ошибок: {len(error_rows)}",
        reply_markup=back_kb(),
    )


@router.callback_query(F.data == "admin:chats")
async def admin_chats(cb: CallbackQuery, settings: Settings, db: LocalDB, bot):
    if not is_admin(cb.from_user.id, settings):
        return await cb.answer("Нет доступа", show_alert=True)

    rows = db.get_chats_snapshot()
    if not rows:
        await cb.message.answer("Группы пока не добавлены.", reply_markup=back_kb())
        await cb.answer("Пусто")
        return

    xlsx_rows = []
    for row in rows:
        xlsx_rows.append(
            [
                str(row["title"] or ""),
                str(row["chat_id"] or ""),
                "approved" if int(row["approved"]) == 1 else "ignored",
                "active" if int(row["is_active"]) == 1 else "inactive",
                str(row["updated_at"] or ""),
            ]
        )

    xlsx = make_xlsx(
        headers=["group_title", "chat_id", "status", "activity", "updated_at"],
        rows=xlsx_rows,
    )
    await bot.send_document(
        cb.message.chat.id,
        BufferedInputFile(xlsx.getvalue(), filename="groups_list.xlsx"),
    )
    await cb.message.answer("Отправил Excel со списком групп ✅", reply_markup=back_kb())
    await cb.answer()
