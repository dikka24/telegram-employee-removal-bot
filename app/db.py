from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

UTC = timezone.utc


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class ManagedChat:
    chat_id: int
    title: str
    chat_type: str
    approved: bool
    is_active: bool


class LocalDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.execute("PRAGMA foreign_keys=ON;")

    def init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS managed_chats (
                chat_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                chat_type TEXT NOT NULL DEFAULT '',
                approved INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 0,
                approved_by TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chat_members (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL DEFAULT '',
                full_name TEXT NOT NULL DEFAULT '',
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY(chat_id, user_id),
                FOREIGN KEY(chat_id) REFERENCES managed_chats(chat_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS deletion_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                telegram_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                result TEXT NOT NULL,
                reason TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS observed_chat_users (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL DEFAULT '',
                full_name TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                last_alerted_at TEXT,
                PRIMARY KEY(chat_id, user_id),
                FOREIGN KEY(chat_id) REFERENCES managed_chats(chat_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_observed_chat_users_last_seen
                ON observed_chat_users(last_seen_at);
            """
        )
        self.conn.commit()
        self._migrate_deletion_log_schema()
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_deletion_log_created_at ON deletion_log(created_at)"
        )
        self.conn.commit()

    def _migrate_deletion_log_schema(self) -> None:
        expected_columns = {"id", "created_at", "telegram_id", "chat_id", "result", "reason"}
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(deletion_log)").fetchall()
        }
        if existing_columns == expected_columns:
            return

        if not expected_columns.issubset(existing_columns):
            missing = ", ".join(sorted(expected_columns - existing_columns))
            raise RuntimeError(f"deletion_log migration failed: missing columns: {missing}")

        cur = self.conn.cursor()
        cur.execute("BEGIN")
        try:
            cur.execute("DROP TABLE IF EXISTS deletion_log_new")
            cur.execute(
                """
                CREATE TABLE deletion_log_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    telegram_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    result TEXT NOT NULL,
                    reason TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                INSERT INTO deletion_log_new(id, created_at, telegram_id, chat_id, result, reason)
                SELECT id, created_at, telegram_id, chat_id, result, reason
                FROM deletion_log
                """
            )
            cur.execute("DROP TABLE deletion_log")
            cur.execute("ALTER TABLE deletion_log_new RENAME TO deletion_log")
        except Exception:
            self.conn.rollback()
            raise
        else:
            self.conn.commit()

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if not row:
            return default
        return str(row["value"])

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            """
            INSERT INTO settings(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        self.conn.commit()

    def upsert_managed_chat(
        self,
        chat_id: int,
        title: str,
        chat_type: str,
        approved: bool,
        is_active: bool,
        approved_by: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO managed_chats(chat_id, title, chat_type, approved, is_active, approved_by, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title = excluded.title,
                chat_type = excluded.chat_type,
                approved = excluded.approved,
                is_active = excluded.is_active,
                approved_by = excluded.approved_by,
                updated_at = excluded.updated_at
            """,
            (
                int(chat_id),
                title or str(chat_id),
                chat_type or "",
                1 if approved else 0,
                1 if is_active else 0,
                approved_by or "",
                _now_iso(),
            ),
        )
        self.conn.commit()

    def set_chat_active(self, chat_id: int, is_active: bool) -> None:
        self.conn.execute(
            "UPDATE managed_chats SET is_active = ?, updated_at = ? WHERE chat_id = ?",
            (1 if is_active else 0, _now_iso(), int(chat_id)),
        )
        self.conn.commit()

    def get_approved_chats(self) -> list[ManagedChat]:
        rows = self.conn.execute(
            "SELECT chat_id, title, chat_type, approved, is_active FROM managed_chats WHERE approved = 1 ORDER BY chat_id"
        ).fetchall()
        return [
            ManagedChat(
                chat_id=int(r["chat_id"]),
                title=str(r["title"] or ""),
                chat_type=str(r["chat_type"] or ""),
                approved=bool(r["approved"]),
                is_active=bool(r["is_active"]),
            )
            for r in rows
        ]

    def get_user_chat_ids(self, user_id: int) -> list[int]:
        rows = self.conn.execute(
            """
            SELECT cm.chat_id
            FROM chat_members cm
            JOIN managed_chats mc ON mc.chat_id = cm.chat_id
            WHERE cm.user_id = ? AND mc.approved = 1 AND mc.is_active = 1
            """,
            (int(user_id),),
        ).fetchall()
        return [int(r["chat_id"]) for r in rows]

    def get_user_memberships(self, user_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT
                cm.chat_id,
                mc.title AS chat_title,
                cm.user_id,
                cm.username,
                cm.full_name
            FROM chat_members cm
            JOIN managed_chats mc ON mc.chat_id = cm.chat_id
            WHERE cm.user_id = ? AND mc.approved = 1 AND mc.is_active = 1
            ORDER BY cm.chat_id
            """,
            (int(user_id),),
        ).fetchall()

    def get_all_active_members(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT
                cm.chat_id,
                mc.title AS chat_title,
                cm.user_id,
                cm.username,
                cm.full_name
            FROM chat_members cm
            JOIN managed_chats mc ON mc.chat_id = cm.chat_id
            WHERE mc.approved = 1 AND mc.is_active = 1
            ORDER BY cm.chat_id, cm.user_id
            """
        ).fetchall()

    def replace_chat_members(self, chat_id: int, members: Iterable[tuple[int, str, str]]) -> None:
        now = _now_iso()
        cur = self.conn.cursor()
        cur.execute("BEGIN")

        for user_id, username, full_name in members:
            cur.execute(
                """
                INSERT INTO chat_members(chat_id, user_id, username, full_name, last_seen_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    username = excluded.username,
                    full_name = excluded.full_name,
                    last_seen_at = excluded.last_seen_at
                """,
                (int(chat_id), int(user_id), username or "", full_name or "", now),
            )

        cur.execute(
            "DELETE FROM chat_members WHERE chat_id = ? AND last_seen_at <> ?",
            (int(chat_id), now),
        )

        cur.execute(
            "UPDATE managed_chats SET is_active = 1, updated_at = ? WHERE chat_id = ?",
            (now, int(chat_id)),
        )
        self.conn.commit()

    def remove_user_from_chat_index(self, chat_id: int, user_id: int) -> None:
        self.conn.execute(
            "DELETE FROM chat_members WHERE chat_id = ? AND user_id = ?",
            (int(chat_id), int(user_id)),
        )
        self.conn.commit()

    def remove_user_from_chat_index_bulk(self, items: Iterable[tuple[int, int]]) -> None:
        payload = [(int(chat_id), int(user_id)) for chat_id, user_id in items]
        if not payload:
            return
        self.conn.executemany(
            "DELETE FROM chat_members WHERE chat_id = ? AND user_id = ?",
            payload,
        )
        self.conn.commit()

    def log_deletion(self, telegram_id: int, chat_id: int, result: str, reason: str) -> None:
        self.conn.execute(
            """
            INSERT INTO deletion_log(created_at, telegram_id, chat_id, result, reason)
            VALUES(?, ?, ?, ?, ?)
            """,
            (_now_iso(), int(telegram_id), int(chat_id), result, reason[:1000]),
        )
        self.conn.commit()

    def log_deletions_bulk(self, items: Iterable[tuple[int, int, str, str]]) -> None:
        now = _now_iso()
        payload = [
            (now, int(telegram_id), int(chat_id), result, reason[:1000])
            for telegram_id, chat_id, result, reason in items
        ]
        if not payload:
            return
        self.conn.executemany(
            """
            INSERT INTO deletion_log(created_at, telegram_id, chat_id, result, reason)
            VALUES(?, ?, ?, ?, ?)
            """,
            payload,
        )
        self.conn.commit()

    def purge_old_deletion_logs(self, retention_days: int) -> int:
        days = max(1, int(retention_days))
        threshold = datetime.now(UTC).timestamp() - (days * 24 * 60 * 60)
        threshold_iso = datetime.fromtimestamp(threshold, UTC).isoformat()
        cur = self.conn.execute(
            "DELETE FROM deletion_log WHERE created_at < ?",
            (threshold_iso,),
        )
        self.conn.commit()
        return int(cur.rowcount or 0)

    def get_chats_snapshot(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT chat_id, title, chat_type, approved, is_active, updated_at FROM managed_chats ORDER BY updated_at DESC"
        ).fetchall()

    def is_chat_approved(self, chat_id: int) -> bool:
        row = self.conn.execute(
            "SELECT approved FROM managed_chats WHERE chat_id = ?",
            (int(chat_id),),
        ).fetchone()
        if not row:
            return False
        return bool(int(row["approved"]))

    def touch_observed_user(
        self,
        chat_id: int,
        user_id: int,
        username: str,
        full_name: str,
    ) -> None:
        now = _now_iso()
        self.conn.execute(
            """
            INSERT INTO observed_chat_users(chat_id, user_id, username, full_name, first_seen_at, last_seen_at)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name,
                last_seen_at = excluded.last_seen_at
            """,
            (
                int(chat_id),
                int(user_id),
                username or "",
                full_name or "",
                now,
                now,
            ),
        )
        self.conn.commit()

    def get_unknown_candidates(self, chat_id: int, limit: int) -> list[sqlite3.Row]:
        safe_limit = max(1, int(limit))
        return self.conn.execute(
            """
            SELECT chat_id, user_id, username, full_name, first_seen_at, last_seen_at, last_alerted_at
            FROM observed_chat_users
            WHERE chat_id = ?
            ORDER BY last_seen_at DESC
            LIMIT ?
            """,
            (int(chat_id), safe_limit),
        ).fetchall()

    def mark_unknown_alerted(self, chat_id: int, user_ids: Iterable[int]) -> None:
        payload = [(int(chat_id), int(user_id)) for user_id in user_ids]
        if not payload:
            return
        now = _now_iso()
        self.conn.executemany(
            """
            UPDATE observed_chat_users
            SET last_alerted_at = ?
            WHERE chat_id = ? AND user_id = ?
            """,
            [(now, chat_id_i, user_id_i) for chat_id_i, user_id_i in payload],
        )
        self.conn.commit()

    def purge_old_observed_users(self, retention_days: int) -> int:
        days = max(1, int(retention_days))
        threshold = datetime.now(UTC).timestamp() - (days * 24 * 60 * 60)
        threshold_iso = datetime.fromtimestamp(threshold, UTC).isoformat()
        cur = self.conn.execute(
            "DELETE FROM observed_chat_users WHERE last_seen_at < ?",
            (threshold_iso,),
        )
        self.conn.commit()
        return int(cur.rowcount or 0)
