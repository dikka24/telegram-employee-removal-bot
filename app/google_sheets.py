from __future__ import annotations

import asyncio
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, TypeVar

import gspread
from gspread.exceptions import APIError
from gspread.utils import rowcol_to_a1
from google.oauth2.service_account import Credentials
from requests import exceptions as req_exceptions

from .models import Employee

T = TypeVar('T')


@dataclass
class SheetRepo:
    gc: gspread.Client
    sheet_id: str

    _sh: Any = field(init=False, repr=False)
    _ws_cache: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _header_cache: dict[str, dict[str, int]] = field(default_factory=dict, init=False, repr=False)
    _tg_column_cache: dict[tuple[str, str, int], tuple[float, set[int]]] = field(default_factory=dict, init=False, repr=False)
    _io_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def __post_init__(self):
        self._sh = self.gc.open_by_key(self.sheet_id)

    @classmethod
    def from_service_account(cls, creds_path: str, sheet_id: str) -> 'SheetRepo':
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive',
        ]
        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
        gc = gspread.authorize(creds)
        # Bound every Google request so a stalled upstream cannot block a worker forever.
        gc.http_client.timeout = (10, 30)
        return cls(gc=gc, sheet_id=sheet_id)

    async def run_async(self, op: Callable[..., T], *args, **kwargs) -> T:
        """Run a synchronous gspread operation away from the Telegram event loop."""

        def _locked_call() -> T:
            with self._io_lock:
                return op(*args, **kwargs)

        return await asyncio.to_thread(_locked_call)

    def _ws(self, worksheet_name: str):
        if worksheet_name in self._ws_cache:
            return self._ws_cache[worksheet_name]

        try:
            ws = self._sh.worksheet(worksheet_name)
        except Exception:
            ws = self._sh.add_worksheet(title=worksheet_name, rows=1000, cols=20)
            if worksheet_name.strip().lower() == 'registration':
                ws.append_row(
                    ['email', 'telegram_id', 'registered_at'],
                    value_input_option='USER_ENTERED',
                )
        self._ws_cache[worksheet_name] = ws
        return ws

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(exc, APIError):
            status_code = getattr(getattr(exc, 'response', None), 'status_code', None)
            return status_code in {429, 500, 502, 503, 504}
        return isinstance(exc, (req_exceptions.ConnectionError, req_exceptions.Timeout))

    def _with_retry(
        self,
        op: Callable[[], T],
        retries: int = 3,
        base_delay: float = 0.5,
        max_delay: float = 8.0,
    ) -> T:
        last_exc: Optional[Exception] = None
        for attempt in range(retries):
            try:
                return op()
            except Exception as exc:
                last_exc = exc
                if attempt == retries - 1 or not self._is_retryable(exc):
                    raise
                delay = min(max_delay, base_delay * (2 ** attempt)) + random.uniform(0, 0.25)
                time.sleep(delay)
        if last_exc:
            raise last_exc
        raise RuntimeError('Retry loop exited unexpectedly')

    def _invalidate_tg_cache(self, ws_name: Optional[str] = None) -> None:
        if ws_name is None:
            self._tg_column_cache.clear()
            return
        ws_norm = (ws_name or '').strip().lower()
        for key in list(self._tg_column_cache.keys()):
            if key[0] == ws_norm:
                self._tg_column_cache.pop(key, None)

    def _header_map(self, ws_name: str) -> dict[str, int]:
        if ws_name in self._header_cache:
            return self._header_cache[ws_name]

        ws = self._ws(ws_name)
        header = self._with_retry(lambda: ws.row_values(1))
        mapping = {name: i + 1 for i, name in enumerate(header) if name}
        self._header_cache[ws_name] = mapping
        return mapping

    def _col_index_by_name(self, ws_name: str, col_name: str) -> Optional[int]:
        target = (col_name or '').strip().lower()
        if not target:
            return None

        header_map = self._header_map(ws_name)
        for name, idx in header_map.items():
            if (name or '').strip().lower() == target:
                return idx
        return None

    def has_tg_in_employees(
        self,
        ws_name: str,
        tg_id: int,
        col_name: str,
        header_row: int = 1,
        cache_ttl_sec: int = 300,
    ) -> bool:
        return self.has_tg_in_sheet(
            ws_name=ws_name,
            tg_id=tg_id,
            col_name=col_name,
            header_row=header_row,
            cache_ttl_sec=cache_ttl_sec,
        )

    def has_tg_in_sheet(
        self,
        ws_name: str,
        tg_id: int,
        col_name: str,
        header_row: int = 1,
        cache_ttl_sec: int = 300,
    ) -> bool:
        ws_norm = (ws_name or '').strip().lower()
        col_norm = (col_name or '').strip().lower()
        cache_key = (ws_norm, col_norm, int(header_row))

        now = time.time()
        cached = self._tg_column_cache.get(cache_key)
        if cached and cached[0] > now:
            return int(tg_id) in cached[1]

        ws = self._ws(ws_name)
        col_idx = self._col_index_by_name(ws_name, col_name)
        if not col_idx:
            if cache_ttl_sec > 0:
                self._tg_column_cache[cache_key] = (now + int(cache_ttl_sec), set())
            return False

        values = self._with_retry(lambda: ws.col_values(col_idx))
        tg_ids: set[int] = set()
        for v in values[max(0, int(header_row)):]:
            s = str(v).strip()
            if s.isdigit():
                tg_ids.add(int(s))

        if cache_ttl_sec > 0:
            self._tg_column_cache[cache_key] = (now + int(cache_ttl_sec), tg_ids)

        return int(tg_id) in tg_ids

    def get_tg_ids_by_column_index(
        self,
        ws_name: str,
        col_index: int,
        header_row: int = 1,
        cache_ttl_sec: int = 300,
    ) -> set[int]:
        safe_col_index = max(1, int(col_index))
        ws_norm = (ws_name or '').strip().lower()
        cache_key = (ws_norm, f'#{safe_col_index}', int(header_row))

        now = time.time()
        cached = self._tg_column_cache.get(cache_key)
        if cached and cached[0] > now:
            return set(cached[1])

        ws = self._ws(ws_name)
        values = self._with_retry(lambda: ws.col_values(safe_col_index))
        tg_ids: set[int] = set()
        for value in values[max(0, int(header_row)):]:
            raw = str(value).strip()
            if raw.isdigit():
                tg_ids.add(int(raw))

        if cache_ttl_sec > 0:
            self._tg_column_cache[cache_key] = (now + int(cache_ttl_sec), tg_ids)

        return tg_ids

    def get_employees(
        self,
        ws_name: str,
        col_full_name: str,
        col_email: str,
        col_status: str,
        col_telegram_id: str,
    ) -> list[Employee]:
        ws = self._ws(ws_name)
        values = self._with_retry(lambda: ws.get_all_values())
        header = self._header_map(ws_name)
        full_name_col = header.get(col_full_name)
        email_col = header.get(col_email)
        status_col = header.get(col_status)
        tg_col = header.get(col_telegram_id)
        employees: list[Employee] = []

        for idx, row in enumerate(values[1:], start=2):
            def _v(col_idx: Optional[int]) -> str:
                if not col_idx:
                    return ""
                pos = col_idx - 1
                if pos < 0 or pos >= len(row):
                    return ""
                return str(row[pos] or "").strip()

            full_name = _v(full_name_col)
            email = _v(email_col).lower()
            status = _v(status_col).lower()

            tg_raw = _v(tg_col)
            tg_id: Optional[int] = None
            try:
                if str(tg_raw).strip():
                    tg_id = int(str(tg_raw).strip())
            except Exception:
                tg_id = None

            if full_name or email:
                employees.append(
                    Employee(
                        full_name=full_name,
                        email=email,
                        status=status,
                        telegram_id=tg_id,
                        row_index=idx,
                    )
                )

        return employees

    def find_employee_by_email(
        self,
        ws_name: str,
        col_full_name: str,
        col_email: str,
        col_status: str,
        col_telegram_id: str,
        email: str,
    ) -> Optional[Employee]:
        target = (email or '').strip().lower()
        for employee in self.get_employees(ws_name, col_full_name, col_email, col_status, col_telegram_id):
            if employee.email == target:
                return employee
        return None

    def upsert_employee_tg(self, ws_name: str, row_index: int, tg_col_name: str, tg_value: int) -> None:
        ws = self._ws(ws_name)
        header_map = self._header_map(ws_name)
        col_idx = header_map.get(tg_col_name)
        if not col_idx:
            raise RuntimeError(f"Колонка '{tg_col_name}' не найдена в листе '{ws_name}'")
        self._with_retry(lambda: ws.update_cell(row_index, col_idx, str(tg_value)))
        self._invalidate_tg_cache(ws_name)

    def upsert_registration(
        self,
        ws_name: str,
        email: str,
        telegram_id: int,
    ) -> None:
        ws = self._ws(ws_name)
        values = self._with_retry(lambda: ws.get_all_values())
        email_norm = (email or '').strip().lower()
        registered_at = datetime.now(timezone.utc).isoformat()

        header_values = [str(value or "").strip() for value in values[0]] if values else []
        required_columns = {"email", "telegram_id", "registered_at"}
        if len(header_values) != 3 or set(header_values) != required_columns:
            raise RuntimeError(
                "Registration sheet must contain only: email, telegram_id, registered_at"
            )

        header = self._header_map(ws_name)
        email_col = int(header["email"])
        telegram_id_col = int(header["telegram_id"])
        registered_at_col = int(header["registered_at"])

        # update existing row by email
        for idx, row in enumerate(values[1:], start=2):
            pos = email_col - 1
            current_email = ""
            if 0 <= pos < len(row):
                current_email = str(row[pos] or "").strip().lower()
            if current_email != email_norm:
                continue

            self._with_retry(
                lambda: ws.batch_update(
                    [
                        {'range': rowcol_to_a1(idx, email_col), 'values': [[email_norm]]},
                        {'range': rowcol_to_a1(idx, telegram_id_col), 'values': [[str(int(telegram_id))]]},
                        {'range': rowcol_to_a1(idx, registered_at_col), 'values': [[registered_at]]},
                    ],
                    value_input_option='USER_ENTERED',
                )
            )
            self._invalidate_tg_cache(ws_name)
            return

        self._with_retry(
            lambda: ws.append_row(
                [
                    {
                        "email": email_norm,
                        "telegram_id": str(int(telegram_id)),
                        "registered_at": registered_at,
                    }[column]
                    for column in header_values
                ],
                value_input_option='USER_ENTERED',
            )
        )
        self._invalidate_tg_cache(ws_name)
