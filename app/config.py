import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


def _split_ints(value: str) -> list[int]:
    if not value:
        return []
    return [int(x.strip()) for x in value.split(",") if x.strip()]


def _split_csv(value: str) -> list[str]:
    if not value:
        return []
    return [x.strip() for x in value.split(",") if x.strip()]


def _to_int(value: Optional[str], default: int) -> int:
    raw = (value or "").strip()
    if not raw:
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    bot_token: str

    google_creds_path: str
    google_sheet_id: str
    sheet_employees: str
    sheet_registration: str
    sheet_cache_ttl_sec: int

    col_full_name: str
    col_email: str
    col_status: str
    col_telegram_id: str

    admin_ids: list[int]

    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    smtp_use_tls: bool
    otp_ttl_seconds: int
    allowed_email_domains: list[str]

    delete_statuses: list[str]

    db_path: str
    status_scan_interval_min: int
    chat_healthcheck_interval_min: int
    chat_healthcheck_batch_size: int

    max_kicks_per_status_run: int
    deletion_log_retention_days: int
    unknown_scan_interval_hours: int
    unknown_user_retention_days: int
    unknown_alert_max_users_per_chat: int


def load_settings() -> Settings:
    allowed_domains = _split_csv(
        os.getenv("ALLOWED_EMAIL_DOMAINS", "uzum.com,uzumteam.uz,apex.com")
    )

    return Settings(
        bot_token=os.environ["BOT_TOKEN"],
        google_creds_path=os.environ["GOOGLE_CREDS_PATH"],
        google_sheet_id=os.environ["GOOGLE_SHEET_ID"],
        sheet_employees=os.getenv("SHEET_EMPLOYEES", "Employees"),
        sheet_registration=os.getenv("SHEET_REGISTRATION", "Registration"),
        sheet_cache_ttl_sec=_to_int(os.getenv("SHEET_CACHE_TTL_SEC"), 300),

        col_full_name=os.getenv("COL_FULL_NAME", "full_name"),
        col_email=os.getenv("COL_EMAIL", "email"),
        col_status=os.getenv("COL_STATUS", "status"),
        col_telegram_id=os.getenv("COL_TELEGRAM_ID", "telegram_id"),

        admin_ids=_split_ints(os.getenv("ADMIN_IDS", "")),

        smtp_host=os.getenv("SMTP_HOST", ""),
        smtp_port=_to_int(os.getenv("SMTP_PORT"), 465),
        smtp_user=os.getenv("SMTP_USER", ""),
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        smtp_from=os.getenv("SMTP_FROM", ""),
        smtp_use_tls=os.getenv("SMTP_USE_TLS", "1") == "1",
        otp_ttl_seconds=_to_int(os.getenv("OTP_TTL_SECONDS"), 600),
        allowed_email_domains=[d.lower() for d in allowed_domains],

        delete_statuses=[s.strip().lower() for s in os.getenv("DELETE_STATUSES", "удалить").split(",") if s.strip()],

        db_path=os.getenv("DB_PATH", "./data/bot.sqlite3"),
        status_scan_interval_min=_to_int(os.getenv("STATUS_SCAN_INTERVAL_MIN"), 10),
        chat_healthcheck_interval_min=_to_int(os.getenv("CHAT_HEALTHCHECK_INTERVAL_MIN"), 10),
        chat_healthcheck_batch_size=_to_int(os.getenv("CHAT_HEALTHCHECK_BATCH_SIZE"), 50),

        max_kicks_per_status_run=_to_int(os.getenv("MAX_KICKS_PER_STATUS_RUN"), 300),
        deletion_log_retention_days=_to_int(os.getenv("DELETION_LOG_RETENTION_DAYS"), 15),
        unknown_scan_interval_hours=_to_int(os.getenv("UNKNOWN_SCAN_INTERVAL_HOURS"), 72),
        unknown_user_retention_days=_to_int(os.getenv("UNKNOWN_USER_RETENTION_DAYS"), 7),
        unknown_alert_max_users_per_chat=_to_int(os.getenv("UNKNOWN_ALERT_MAX_USERS_PER_CHAT"), 20),
    )
