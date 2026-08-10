import time

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from ..config import Settings
from ..google_sheets import SheetRepo
from ..utils.validators import is_allowed_corporate_email, is_valid_email, normalize_email
from .email_otp import generate_otp, send_otp_email

router = Router()


class RegState(StatesGroup):
    waiting_email = State()
    waiting_otp = State()


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, settings: Settings, repo: SheetRepo):
    await state.clear()
    tg_id = message.from_user.id
    try:
        progress = await message.answer("Проверяю ваш аккаунт в базе, вернусь с ответом...")
    except Exception:
        # User may have blocked the bot; nothing to do in this dialog.
        return

    try:
        is_registered = repo.has_tg_in_sheet(
            ws_name=settings.sheet_employees,
            tg_id=tg_id,
            col_name=settings.col_telegram_id,
            header_row=1,
            cache_ttl_sec=settings.sheet_cache_ttl_sec,
        )
    except Exception:
        await progress.edit_text("Сервис временно перегружен. Попробуйте снова через 1-2 минуты.")
        return

    if is_registered:
        await progress.edit_text("Вы уже зарегистрированы ✅")
        return

    await state.set_state(RegState.waiting_email)
    await progress.edit_text("Введите корпоративную почту для регистрации:")


@router.message(RegState.waiting_email, F.text)
async def process_email(message: Message, state: FSMContext, settings: Settings):
    email = normalize_email(message.text or "")

    if not is_valid_email(email):
        await message.answer("Похоже, это не email. Введите корректную корпоративную почту:")
        return

    if not is_allowed_corporate_email(email, settings.allowed_email_domains):
        domains = ", ".join(f"@{d}" for d in settings.allowed_email_domains)
        await message.answer(
            "Разрешены только корпоративные почты:\n"
            f"{domains}\n\n"
            "Введите корпоративную почту ещё раз:"
        )
        return

    otp = generate_otp()
    try:
        send_otp_email(settings, email, otp)
    except Exception as e:
        await state.clear()
        await message.answer(f"Не удалось отправить код. Ошибка: {type(e).__name__}")
        return

    await state.set_state(RegState.waiting_otp)
    await state.update_data(
        reg_email=email,
        otp_code=otp,
        otp_sent_at=int(time.time()),
        otp_attempts=0,
    )
    await message.answer("Код отправлен на почту. Введите 6-значный код:")



@router.message(RegState.waiting_otp, F.text)
async def process_otp(message: Message, state: FSMContext, settings: Settings, repo: SheetRepo):
    data = await state.get_data()

    otp_code = str(data.get("otp_code", ""))
    sent_at = int(data.get("otp_sent_at", 0))
    attempts = int(data.get("otp_attempts", 0))
    reg_email = str(data.get("reg_email", ""))

    if int(time.time()) - sent_at > settings.otp_ttl_seconds:
        await state.clear()
        await message.answer("Код истёк. Начните заново: /start")
        return

    user_code = (message.text or "").strip()

    if user_code != otp_code:
        attempts += 1
        await state.update_data(otp_attempts=attempts)

        if attempts >= 2:
            await state.clear()
            await message.answer("Неверный код. Начните заново командой /start")
            return

        await message.answer("Неверный код. Попробуйте ещё раз:")
        return

    try:
        repo.upsert_registration(
            ws_name=settings.sheet_registration,
            email=reg_email,
            telegram_id=message.from_user.id,
        )
    except Exception:
        await state.clear()
        await message.answer("Не удалось завершить регистрацию из-за временной перегрузки. Попробуйте снова через 1-2 минуты.")
        return


    await state.clear()
    await message.answer("Регистрация прошла успешно ✅")
