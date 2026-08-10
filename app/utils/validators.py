import re

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match(normalize_email(email)))


def is_allowed_corporate_email(email: str, allowed_domains: list[str]) -> bool:
    normalized = normalize_email(email)
    if "@" not in normalized:
        return False
    domain = normalized.split("@", 1)[1]
    return domain in {d.strip().lower() for d in allowed_domains if d.strip()}
