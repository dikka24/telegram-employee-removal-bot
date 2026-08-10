from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup

from ..config import Settings
from ..db import LocalDB

router = Router()


def _kb(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да", callback_data=f"chat:approve:{chat_id}"),
                InlineKeyboardButton(text="❌ Нет", callback_data=f"chat:ignore:{chat_id}"),
            ]
        ]
    )


def _approved_by_text(user) -> str:
    name = (user.full_name or "").strip()
    if user.username:
        return f"{name} (@{user.username})"
    return name


async def _is_main_bot_ready(bot, chat_id: int, bot_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=bot_id)
    except Exception:
        return False

    status = getattr(member, "status", "")
    if status == "creator":
        return True
    if status != "administrator":
        return False

    can_restrict = getattr(member, "can_restrict_members", None)
    # For legacy/group edge-cases where Telegram omits the flag, admin status is enough.
    if can_restrict is None:
        return True
    return bool(can_restrict)


async def _is_chat_active(bot, chat_id: int, bot_id: int) -> bool:
    return await _is_main_bot_ready(bot, chat_id, bot_id)


async def refresh_chat_activity(settings: Settings, db: LocalDB, bot) -> None:
    chats = db.get_approved_chats()
    if not chats:
        return
    me = await bot.get_me()
    bot_id = int(me.id)

    batch_size = max(1, settings.chat_healthcheck_batch_size)
    total = len(chats)
    cursor = int(db.get_setting("healthcheck_cursor", "0") or 0)
    start = cursor % total

    batch: list = []
    idx = start
    for _ in range(min(batch_size, total)):
        batch.append(chats[idx])
        idx = (idx + 1) % total

    for chat in batch:
        is_active = await _is_chat_active(bot, chat.chat_id, bot_id)
        db.set_chat_active(chat.chat_id, is_active)

    db.set_setting("healthcheck_cursor", str((start + len(batch)) % total))


@router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated, settings: Settings, bot, db: LocalDB):
    old_status = getattr(event.old_chat_member, "status", None)
    new_status = getattr(event.new_chat_member, "status", None)

    if new_status != "administrator" or old_status == "administrator":
        return

    chat = event.chat
    chat_id = chat.id
    title = chat.title or chat.full_name or str(chat_id)
    chat_type = chat.type

    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=(
                    "Мне выдали права администратора в чате:\n"
                    f"• Название: {title}\n"
                    f"• Тип: {chat_type}\n"
                    f"• chat_id: {chat_id}\n\n"
                    "Активировать работу в этой группе?"
                ),
                reply_markup=_kb(chat_id),
            )
        except Exception as e:
            # Admin may not have opened a private chat with the bot yet.
            print(f"admin notify failed for admin_id={admin_id}: {type(e).__name__}: {e}")


@router.callback_query(F.data.startswith("chat:"))
async def chat_decision(cb: CallbackQuery, settings: Settings, bot, db: LocalDB):
    if cb.from_user.id not in settings.admin_ids:
        return await cb.answer("Нет доступа", show_alert=True)

    _, action, chat_id_str = cb.data.split(":")
    chat_id = int(chat_id_str)

    title = str(chat_id)
    chat_type = ""
    try:
        chat = await bot.get_chat(chat_id)
        title = chat.title or chat.full_name or str(chat_id)
        chat_type = chat.type
    except Exception:
        pass

    approved = action == "approve"
    is_active = False
    me = await bot.get_me()
    bot_id = int(me.id)

    if approved:
        is_active = await _is_chat_active(bot, chat_id, bot_id)

    db.upsert_managed_chat(
        chat_id=chat_id,
        title=title,
        chat_type=chat_type,
        approved=approved,
        is_active=is_active,
        approved_by=_approved_by_text(cb.from_user),
    )

    if not approved:
        text = f"Сохранено ✅\n{title}\nchat_id: {chat_id}\nstatus: ignored"
    elif is_active:
        text = f"Сохранено ✅\n{title}\nchat_id: {chat_id}\nstatus: approved + active"
    else:
        text = (
            f"Сохранено ✅\n{title}\nchat_id: {chat_id}\n"
            "status: approved, но INACTIVE\n\n"
            "Проверьте: у бота есть права администратора "
            "(в том числе ограничение участников), "
            "и бот не удалён из группы.\n"
            "После этого чат автоматически станет active на healthcheck."
        )

    await cb.message.edit_text(text)
    await cb.answer("Готово")
