"""
message_store.py — SQLite-хранилище сообщений Delo Space.

Сохраняет входящие сообщения бота и позволяет получать их
извне (например, с ПК по VPN) через HTTP-эндпоинт.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "data" / "messages.db"


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_id TEXT UNIQUE,
            chat_id TEXT,
            chat_type TEXT,
            sender_id TEXT,
            sender_name TEXT,
            body TEXT,
            command TEXT,
            raw_data TEXT,
            received_at TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_chat
        ON messages(chat_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_time
        ON messages(received_at)
    """)
    conn.commit()


def store_message(
    sync_id: str,
    chat_id: str,
    chat_type: str,
    sender_id: str,
    sender_name: str,
    body: str,
    command: Optional[str] = None,
    raw_data: Optional[dict] = None,
) -> bool:
    """Сохраняет входящее сообщение. Возвращает True если добавлено новое."""
    conn = _conn()
    try:
        # Пропускаем дубликаты по sync_id
        cur = conn.execute("SELECT 1 FROM messages WHERE sync_id=?", (sync_id,))
        if cur.fetchone():
            return False

        conn.execute(
            """
            INSERT OR IGNORE INTO messages
                (sync_id, chat_id, chat_type, sender_id, sender_name, body, command, raw_data, received_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sync_id,
                chat_id,
                chat_type,
                sender_id,
                sender_name,
                body,
                command,
                json.dumps(raw_data, ensure_ascii=False) if raw_data else None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def list_messages(
    chat_id: Optional[str] = None,
    limit: int = 100,
    after_id: Optional[int] = None,
) -> list[dict]:
    """Возвращает сообщения, опционально фильтруя по чату и после ID."""
    conn = _conn()
    try:
        query = "SELECT * FROM messages WHERE 1=1"
        params: list = []
        if chat_id:
            query += " AND chat_id=?"
            params.append(chat_id)
        if after_id:
            query += " AND id>?"
            params.append(after_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_message(message_id: int) -> Optional[dict]:
    """Возвращает сообщение по ID."""
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def count_messages() -> int:
    """Количество сохранённых сообщений."""
    conn = _conn()
    try:
        row = conn.execute("SELECT COUNT(*) as c FROM messages").fetchone()
        return row["c"] if row else 0
    finally:
        conn.close()