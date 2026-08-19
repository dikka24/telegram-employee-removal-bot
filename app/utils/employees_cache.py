from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Optional

from ..google_sheets import SheetRepo
from ..config import Settings
from .validators import normalize_email


@dataclass(frozen=True)
class EmployeeInfo:
    full_name: str
    email: str
    telegram_id: Optional[int]


class EmployeesCache:
    """
    Кэш сотрудников (Employees) в памяти на TTL секунд.
    Держит:
      - email -> EmployeeInfo
      - set(tg_id)
    """
    def __init__(self, ttl_seconds: int = 300):
        self.ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()
        self._expires_at = 0.0
        self._by_email: dict[str, EmployeeInfo] = {}
        self._tg_ids: set[int] = set()

    def _is_fresh(self) -> bool:
        return time.time() < self._expires_at and bool(self._by_email)

    async def ensure(self, repo: SheetRepo, settings: Settings) -> None:
        if self._is_fresh():
            return
        async with self._lock:
            if self._is_fresh():
                return
            employees = await repo.run_async(
                repo.get_employees,
                settings.sheet_employees,
                settings.col_full_name,
                settings.col_email,
                settings.col_status,
                settings.col_telegram_id,
            )

            by_email: dict[str, EmployeeInfo] = {}
            tg_ids: set[int] = set()

            for e in employees:
                em = normalize_email(e.email or "")
                info = EmployeeInfo(full_name=e.full_name or "", email=em, telegram_id=e.telegram_id)
                if em:
                    by_email[em] = info
                if e.telegram_id:
                    tg_ids.add(int(e.telegram_id))

            self._by_email = by_email
            self._tg_ids = tg_ids
            self._expires_at = time.time() + self.ttl_seconds

    async def get_by_email(self, repo: SheetRepo, settings: Settings, email: str) -> Optional[EmployeeInfo]:
        await self.ensure(repo, settings)
        return self._by_email.get(normalize_email(email))

    async def has_tg_id(self, repo: SheetRepo, settings: Settings, tg_id: int) -> bool:
        await self.ensure(repo, settings)
        return int(tg_id) in self._tg_ids

    async def invalidate(self) -> None:
        async with self._lock:
            self._expires_at = 0.0
            self._by_email = {}
            self._tg_ids = set()


# глобальный кэш (один на процесс)
employees_cache = EmployeesCache(ttl_seconds=300)
