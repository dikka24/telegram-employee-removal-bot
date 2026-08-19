# Telegram HR Access Bot (BOT-only)

Проект работает одним сервисом `bot` (aiogram).

## Что делает бот

1. Регистрация пользователя через `/start`:
- сразу отправляет сообщение: "Проверяю ваш аккаунт в базе, вернусь с ответом...";
- если `telegram_id` уже есть в `Employees` (`COL_TELEGRAM_ID`) — пишет, что пользователь уже зарегистрирован;
- если нет — просит корпоративную почту, проверяет домен, отправляет OTP, и при успехе фиксирует запись в `Registration`.

2. Активация групп:
- когда боту дали админ-права, админам (`ADMIN_IDS`) приходит approve/ignore;
- после approve группа попадает в локальную БД как управляемая.

3. Автоудаление по статусу:
- по расписанию читает `Employees`;
- если `status` входит в `DELETE_STATUSES`, берёт `telegram_id` из `Employees`;
- бот проходит по всем approved-группам и пытается кикнуть этот `telegram_id`.

4. Ручное удаление по почте (админка):
- админ вводит корпоративную почту;
- бот находит `telegram_id` в `Employees`;
- проходит по approved-группам и пытается удалить;
- отправляет Excel-отчёт.

5. Ежедневный алерт по неизвестным пользователям:
- бот ловит `user_id` из `message.from_user`, `new_chat_members`, `callback_query.from_user` в approved-группах;
- хранит наблюдения в локальной БД (без ботов);
- раз в день сверяет с `Employees.COL_TELEGRAM_ID`;
- если пользователя нет в базе сотрудников, отправляет алерт в тот же чат с упоминанием.

## Что убрано

- отдельный `userbot` процесс;
- MTProto-сессия как обязательная часть запуска;
- пункт админки "Удаление неизвестных пользователей".

## Google Sheets

Нужны 2 листа в одной таблице:
- `Employees` (`SHEET_EMPLOYEES`) — источник сотрудников/статусов/telegram_id;
- `Registration` (`SHEET_REGISTRATION`) — фиксация успешной регистрации.

Запросы Google Sheets выполняются в отдельном worker thread с HTTP timeout, поэтому задержка Google API не останавливает Telegram polling.

## Watchdog

Бот обновляет `data/bot_heartbeat` каждую минуту. Cron-скрипт `scripts/watchdog_polling.sh` проверяет heartbeat и Telegram `getMe` каждые 5 минут. При heartbeat старше 3 минут контейнер перезапускается сразу.

### Обязательные колонки `Employees`
- `COL_FULL_NAME`
- `COL_EMAIL`
- `COL_STATUS`
- `COL_TELEGRAM_ID`

### Колонки `Registration`
- `email`
- `telegram_id`
- `registered_at`

## `.env`

Скопируйте шаблон:
```bash
cp .env.example .env
```

Ключевые параметры:
- `BOT_TOKEN`
- `ADMIN_IDS`
- `GOOGLE_CREDS_PATH`
- `GOOGLE_SHEET_ID`
- `SHEET_EMPLOYEES`
- `SHEET_REGISTRATION`
- `SHEET_CACHE_TTL_SEC`
- `COL_FULL_NAME`
- `COL_EMAIL`
- `COL_STATUS`
- `COL_TELEGRAM_ID`
- `SMTP_HOST`
- `SMTP_PORT=465`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `SMTP_FROM`
- `SMTP_USE_TLS=0` (для 465)
- `OTP_TTL_SECONDS`
- `ALLOWED_EMAIL_DOMAINS`
- `DELETE_STATUSES`
- `STATUS_SCAN_INTERVAL_MIN`
- `CHAT_HEALTHCHECK_INTERVAL_MIN`
- `CHAT_HEALTHCHECK_BATCH_SIZE`
- `MAX_KICKS_PER_STATUS_RUN`
- `DELETION_LOG_RETENTION_DAYS` (по умолчанию 7 дней)
- `UNKNOWN_SCAN_INTERVAL_HOURS`
- `UNKNOWN_USER_RETENTION_DAYS`
- `UNKNOWN_ALERT_MAX_USERS_PER_CHAT`
- `DB_PATH`

## Docker запуск

```bash
docker-compose build
docker-compose up -d bot
docker-compose logs -f bot
```

Остановка:
```bash
docker-compose down
```

## Важно

Для удаления пользователей бот должен быть администратором в целевых группах и иметь право удаления участников.
