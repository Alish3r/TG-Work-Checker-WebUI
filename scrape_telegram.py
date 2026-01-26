import os
import asyncio
import sqlite3
import argparse
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlparse

from telethon import TelegramClient
from telethon.errors import (
    UsernameInvalidError,
    UsernameNotOccupiedError,
    ChannelPrivateError,
    FloodWaitError,
    AuthRestartError,
)
from telethon.tl.custom.message import Message

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# Set up logging
logger = logging.getLogger(__name__)


def load_config(cli_args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    # Load from .env if python-dotenv is available and file exists
    if load_dotenv is not None and os.path.exists(".env"):
        # Override any empty or pre-set values in the current process
        load_dotenv(override=True)

    def getenv_robust(name: str):
        # Windows PowerShell "UTF8" files often include a BOM; python-dotenv can treat it as part of the key.
        return os.getenv(name) or os.getenv("\ufeff" + name)

    def env_int(name, default=None):
        value = getenv_robust(name)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            raise ValueError(f"Environment variable {name} must be an integer, got: {value!r}")

    cli_args = cli_args or {}

    api_id = env_int("API_ID")
    api_hash = getenv_robust("API_HASH")
    session_name = cli_args.get("session_name") or getenv_robust("SESSION_NAME") or "telethon_session"
    chat_identifier_raw = cli_args.get("chat") or getenv_robust("CHAT_IDENTIFIER")
    topic_id_env = cli_args.get("topic_id")
    if topic_id_env is None:
        topic_id_env = env_int("TOPIC_ID")
    output_db = cli_args.get("output_db") or getenv_robust("OUTPUT_DB") or "telegram_messages.db"
    output_csv = cli_args.get("output_csv") or getenv_robust("OUTPUT_CSV") or "telegram_messages.csv"
    days_back = cli_args.get("days_back")
    if days_back is None:
        days_back = env_int("DAYS_BACK", 30)
    edit_lookback_days = cli_args.get("edit_lookback_days")
    if edit_lookback_days is None:
        # If not set, the scraper will default to DAYS_BACK later
        edit_lookback_days = env_int("EDIT_LOOKBACK_DAYS")

    if api_id is None or not api_hash:
        raise RuntimeError("Please set API_ID and API_HASH in environment variables or .env file.")

    if not chat_identifier_raw:
        raise RuntimeError("Please set CHAT_IDENTIFIER in environment variables or .env file.")

    chat_identifier, topic_id = parse_chat_identifier(chat_identifier_raw, topic_id_env)

    return {
        "api_id": api_id,
        "api_hash": api_hash,
        "session_name": session_name,
        "chat_identifier": chat_identifier,
        "topic_id": topic_id,
        "output_db": output_db,
        "output_csv": output_csv,
        "days_back": days_back,
        "edit_lookback_days": edit_lookback_days,
    }


def init_db(db_path: str):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    def rebuild_messages_table_for_topics():
        """
        One-time migration for legacy DBs where messages table was created with a table-level
        UNIQUE(chat_identifier, message_id) constraint. That constraint can't be dropped via DROP INDEX,
        so we rebuild the table with the current schema.
        """
        print("Migrating DB: rebuilding messages table to support topics (one-time)...")
        cur.execute("BEGIN")
        # Ensure any previous partial migration is cleaned up
        cur.execute("DROP TABLE IF EXISTS messages_new")

        cur.execute(
            """
            CREATE TABLE messages_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                chat_identifier TEXT NOT NULL,
                topic_id INTEGER NOT NULL DEFAULT -1,
                message_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                edit_date TEXT,
                sender_id INTEGER,
                sender_username TEXT,
                text TEXT,
                reply_to_msg_id INTEGER,
                is_service INTEGER DEFAULT 0,
                deleted INTEGER DEFAULT 0,
                updated_at TEXT
            )
            """
        )

        # Copy data from old table, best-effort mapping (topic_id may not exist on old DB; treat as -1)
        cur.execute("PRAGMA table_info(messages)")
        cols = {row[1] for row in cur.fetchall()}
        has_chat_id = "chat_id" in cols
        has_topic_id = "topic_id" in cols
        has_edit_date = "edit_date" in cols
        has_deleted = "deleted" in cols
        has_updated_at = "updated_at" in cols

        select_chat_id = "chat_id" if has_chat_id else "NULL AS chat_id"
        select_topic_id = "COALESCE(topic_id, -1) AS topic_id" if has_topic_id else "-1 AS topic_id"
        select_edit_date = "edit_date" if has_edit_date else "NULL AS edit_date"
        select_deleted = "COALESCE(deleted, 0) AS deleted" if has_deleted else "0 AS deleted"
        select_updated_at = "updated_at" if has_updated_at else "NULL AS updated_at"

        # Dedupe by (chat_identifier, topic_id, message_id) and keep the earliest row
        cur.execute(
            f"""
            INSERT INTO messages_new (
                chat_id, chat_identifier, topic_id, message_id, date, edit_date,
                sender_id, sender_username, text, reply_to_msg_id, is_service, deleted, updated_at
            )
            SELECT
                {select_chat_id},
                chat_identifier,
                {select_topic_id},
                message_id,
                date,
                {select_edit_date},
                sender_id,
                sender_username,
                text,
                reply_to_msg_id,
                COALESCE(is_service, 0) AS is_service,
                {select_deleted},
                {select_updated_at}
            FROM messages
            WHERE id IN (
                SELECT MIN(id)
                FROM messages
                GROUP BY chat_identifier, {select_topic_id.split(' AS ')[0]}, message_id
            )
            """
        )

        cur.execute("ALTER TABLE messages RENAME TO messages_legacy")
        cur.execute("ALTER TABLE messages_new RENAME TO messages")
        cur.execute("DROP TABLE messages_legacy")
        cur.execute("COMMIT")
        print("Migration complete.")

    # Lightweight migrations for existing DBs (SQLite doesn't support ADD COLUMN IF NOT EXISTS).
    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='messages'")
    row = cur.fetchone()
    messages_sql = (row[0] if row else "") or ""
    # Detect legacy table-level UNIQUE(chat_identifier, message_id). This often appears as an
    # autoindex (sqlite_autoindex_*) and cannot be dropped; we must rebuild the table.
    needs_rebuild = (
        "unique" in messages_sql.lower()
        and "chat_identifier" in messages_sql.lower()
        and "message_id" in messages_sql.lower()
        and "topic_id" not in messages_sql.lower()
    )

    if not needs_rebuild:
        cur.execute("PRAGMA index_list(messages)")
        for (_, idx_name, is_unique, *_rest) in cur.fetchall():
            if not is_unique:
                continue
            cur.execute(f"PRAGMA index_info({idx_name})")
            cols = [r[2] for r in cur.fetchall()]
            if cols == ["chat_identifier", "message_id"]:
                needs_rebuild = True
                break

    if needs_rebuild:
        rebuild_messages_table_for_topics()

    cur.execute("PRAGMA table_info(messages)")
    existing_cols = {row[1] for row in cur.fetchall()}  # row[1] = name

    def add_column(col_name: str, col_def: str):
        if col_name not in existing_cols:
            cur.execute(f"ALTER TABLE messages ADD COLUMN {col_name} {col_def}")
            existing_cols.add(col_name)

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            chat_identifier TEXT NOT NULL,
            topic_id INTEGER NOT NULL DEFAULT -1,
            message_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            edit_date TEXT,
            sender_id INTEGER,
            sender_username TEXT,
            text TEXT,
            reply_to_msg_id INTEGER,
            is_service INTEGER DEFAULT 0,
            deleted INTEGER DEFAULT 0,
            updated_at TEXT
        )
        """
    )

    # Refresh columns after creating the table (new DBs will have all columns already).
    cur.execute("PRAGMA table_info(messages)")
    existing_cols = {row[1] for row in cur.fetchall()}

    # If table existed before, ensure new columns exist.
    add_column("chat_id", "INTEGER")
    add_column("topic_id", "INTEGER")
    add_column("edit_date", "TEXT")
    add_column("deleted", "INTEGER DEFAULT 0")
    add_column("updated_at", "TEXT")
    # Normalize old NULL topic_id values so we can use a proper UNIQUE constraint.
    cur.execute("UPDATE messages SET topic_id = -1 WHERE topic_id IS NULL")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_chat_date ON messages(chat_identifier, date)"
    )
    # Prevent duplicates across runs.
    # If the DB already contains duplicates from older runs, dedupe first so we can add a UNIQUE index safely.
    cur.execute(
        """
        DELETE FROM messages
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM messages
            GROUP BY chat_identifier, message_id, topic_id
        )
        """
    )
    # Ensure we have a plain UNIQUE index on exactly (chat_identifier, topic_id, message_id).
    # Older versions may have created an expression-based index which SQLite cannot use for ON CONFLICT.
    cur.execute("PRAGMA index_list(messages)")
    indexes = cur.fetchall()
    has_plain_unique = False
    for (_, idx_name, is_unique, *_rest) in indexes:
        if not is_unique:
            continue
        # Try to detect legacy unique indexes that don't include topic_id.
        # index_info can miss expression indexes, so also fall back to sqlite_master.sql.
        cur.execute(f"PRAGMA index_info({idx_name})")
        cols = [r[2] for r in cur.fetchall()]  # r[2] = column name (None for expressions)
        cur.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (idx_name,))
        idx_sql_row = cur.fetchone()
        idx_sql = (idx_sql_row[0] if idx_sql_row else "") or ""
        idx_sql_l = idx_sql.lower()

        is_legacy_chat_msg = (
            cols == ["chat_identifier", "message_id"]
            or (
                "unique" in idx_sql_l
                and "messages" in idx_sql_l
                and "chat_identifier" in idx_sql_l
                and "message_id" in idx_sql_l
                and "topic_id" not in idx_sql_l
            )
        )
        if is_legacy_chat_msg:
            cur.execute(f"DROP INDEX IF EXISTS {idx_name}")
            continue

        if cols == ["chat_identifier", "topic_id", "message_id"]:
            has_plain_unique = True
            break

    if not has_plain_unique:
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_messages_chat_topic_msgid_plain ON messages(chat_identifier, topic_id, message_id)"
        )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_chat_topic_date ON messages(chat_identifier, topic_id, date)"
    )

    # Checkpoints for faster incremental runs
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS checkpoints (
            chat_identifier TEXT NOT NULL,
            topic_id INTEGER NOT NULL DEFAULT -1,
            last_message_id INTEGER DEFAULT 0,
            last_run_ts TEXT,
            PRIMARY KEY (chat_identifier, topic_id)
        )
        """
    )
    conn.commit()
    return conn


def parse_chat_identifier(raw_identifier: str, topic_id_env: Optional[int] = None) -> Tuple[str, Optional[int]]:
    """
    Supports plain usernames/IDs or full t.me links.
    If a message/topic id is present in the URL, use it as topic_id (thread).
    """
    topic_id = topic_id_env
    identifier = raw_identifier.strip()

    if identifier.startswith(("http://", "https://")):
        parsed = urlparse(identifier)
        parts = [p for p in parsed.path.split("/") if p]
        if parts:
            identifier = parts[0]  # username or channel slug
            if len(parts) >= 2 and topic_id is None:
                try:
                    topic_id = int(parts[1])
                except ValueError:
                    pass

    return identifier, topic_id


def _topic_id_norm(topic_id: Optional[int]) -> int:
    """Normalize topic_id to int, returning -1 if None."""
    return int(topic_id) if topic_id is not None else -1


async def fetch_messages(config: Dict[str, Any]) -> None:
    api_id = config["api_id"]
    api_hash = config["api_hash"]
    session_name = config["session_name"]
    chat_identifier = config["chat_identifier"]
    topic_id = _topic_id_norm(config.get("topic_id"))
    db_path = config["output_db"]
    csv_path = config.get("output_csv")
    days_back = config["days_back"]
    edit_lookback_days_cfg = config.get("edit_lookback_days")

    conn = init_db(db_path)
    cur = conn.cursor()

    client = TelegramClient(session_name, api_id, api_hash)

    phone_or_token = (
        os.getenv("PHONE_OR_TOKEN")
        or os.getenv("PHONE")
        or os.getenv("BOT_TOKEN")
        or None
    )

    if phone_or_token:
        await client.start(phone=phone_or_token)
    else:
        await client.start()

    try:
        entity = await client.get_entity(chat_identifier)
    except (UsernameInvalidError, UsernameNotOccupiedError, ChannelPrivateError) as e:
        await client.disconnect()
        raise RuntimeError(f"Unable to access chat {chat_identifier!r}: {e}")

    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=days_back)
    run_ts = now_utc.isoformat()

    scope = f"{chat_identifier}"
    if topic_id is not None:
        scope += f" (topic/thread id: {topic_id})"

    logger.info(f"Scraping messages from {scope} for the last {days_back} days...")
    logger.info(f"Cutoff date (UTC): {cutoff.isoformat()}")
    print(f"Scraping messages from {scope} for the last {days_back} days...")
    print(f"Cutoff date (UTC): {cutoff.isoformat()}")

    iter_kwargs = {}
    if topic_id != -1:
        # Telethon uses reply_to/thread for forum topics
        iter_kwargs["reply_to"] = topic_id

    # Load checkpoint
    cur.execute(
        "SELECT COALESCE(last_message_id, 0), last_run_ts FROM checkpoints WHERE chat_identifier=? AND topic_id=?",
        (chat_identifier, topic_id),
    )
    cp = cur.fetchone()
    last_msg_id = cp[0] if cp else 0
    last_run_ts = cp[1] if cp else None

    scanned = 0
    inserted = 0
    updated = 0
    seen_ids = set()
    max_seen_id = last_msg_id

    # Batch upserts (insert + update-on-change)
    UPSERT_SQL = """
    INSERT INTO messages (
        chat_id,
        chat_identifier,
        topic_id,
        message_id,
        date,
        edit_date,
        sender_id,
        sender_username,
        text,
        reply_to_msg_id,
        is_service,
        deleted,
        updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
    ON CONFLICT(chat_identifier, topic_id, message_id) DO UPDATE SET
        chat_id=excluded.chat_id,
        date=excluded.date,
        edit_date=excluded.edit_date,
        sender_id=excluded.sender_id,
        sender_username=excluded.sender_username,
        text=excluded.text,
        reply_to_msg_id=excluded.reply_to_msg_id,
        is_service=excluded.is_service,
        deleted=0,
        updated_at=excluded.updated_at
    WHERE
        COALESCE(messages.edit_date, '') != COALESCE(excluded.edit_date, '')
        OR COALESCE(messages.text, '') != COALESCE(excluded.text, '')
        OR COALESCE(messages.sender_id, -1) != COALESCE(excluded.sender_id, -1)
        OR COALESCE(messages.sender_username, '') != COALESCE(excluded.sender_username, '')
        OR COALESCE(messages.reply_to_msg_id, -1) != COALESCE(excluded.reply_to_msg_id, -1)
        OR COALESCE(messages.is_service, 0) != COALESCE(excluded.is_service, 0)
        OR COALESCE(messages.deleted, 0) != 0
    """

    upsert_rows = []

    def flush(batch_commit=False):
        nonlocal updated
        if not upsert_rows:
            return
        before = conn.total_changes
        cur.executemany(UPSERT_SQL, upsert_rows)
        delta = conn.total_changes - before
        # delta counts inserts+updates. "inserted" is counted separately; treat the remainder as updates.
        updated += max(0, delta - inserted)
        upsert_rows.clear()
        if batch_commit:
            conn.commit()

    chat_id = int(getattr(entity, "id", 0)) if getattr(entity, "id", None) is not None else None

    # Pass A: fetch only NEW messages since last checkpoint (fast)
    try:
        async for msg in client.iter_messages(entity, min_id=last_msg_id, reverse=False, **iter_kwargs):
            if not isinstance(msg, Message) or msg.date is None:
                continue
            if msg.date < cutoff:
                break
            if msg.id <= last_msg_id:
                continue
            seen_ids.add(msg.id)
            max_seen_id = max(max_seen_id, msg.id)

            text = msg.message or ""
            is_service = 1 if msg.action is not None else 0
            edit_date = msg.edit_date.isoformat() if getattr(msg, "edit_date", None) else None

            # New record check (cheap)
            cur.execute(
                "SELECT 1 FROM messages WHERE chat_identifier=? AND topic_id=? AND message_id=? LIMIT 1",
                (chat_identifier, topic_id, msg.id),
            )
            if cur.fetchone() is None:
                inserted += 1

            upsert_rows.append(
                (
                    chat_id,
                    chat_identifier,
                    topic_id,
                    msg.id,
                    msg.date.isoformat(),
                    edit_date,
                    msg.sender_id if hasattr(msg, "sender_id") else None,
                    getattr(getattr(msg, "sender", None), "username", None),
                    text,
                    msg.reply_to_msg_id,
                    is_service,
                    run_ts,
                )
            )
            scanned += 1
            if scanned % 300 == 0:
                flush(batch_commit=True)
                print(f"{scanned} scanned ({inserted} new, {updated} updated)...")
    except FloodWaitError as e:
        print(f"Rate limited by Telegram. Sleeping for {e.seconds}s...")
        await asyncio.sleep(e.seconds)
    except AuthRestartError:
        print("Telegram requested auth restart; retrying once...")
        await client.disconnect()
        await client.connect()

    # Pass B: scan recent window for edits/deletes (default = DAYS_BACK; tune with EDIT_LOOKBACK_DAYS)
    edit_lookback_days = int(edit_lookback_days_cfg) if edit_lookback_days_cfg is not None else int(os.getenv("EDIT_LOOKBACK_DAYS", str(days_back)))
    edit_cutoff = now_utc - timedelta(days=edit_lookback_days)

    try:
        async for msg in client.iter_messages(entity, reverse=False, **iter_kwargs):
            if not isinstance(msg, Message) or msg.date is None:
                continue
            if msg.date < edit_cutoff:
                break

            seen_ids.add(msg.id)
            max_seen_id = max(max_seen_id, msg.id)

            text = msg.message or ""
            is_service = 1 if msg.action is not None else 0
            edit_date = msg.edit_date.isoformat() if getattr(msg, "edit_date", None) else None

            upsert_rows.append(
                (
                    chat_id,
                    chat_identifier,
                    topic_id,
                    msg.id,
                    msg.date.isoformat(),
                    edit_date,
                    msg.sender_id if hasattr(msg, "sender_id") else None,
                    getattr(getattr(msg, "sender", None), "username", None),
                    text,
                    msg.reply_to_msg_id,
                    is_service,
                    run_ts,
                )
            )
            scanned += 1
            if scanned % 300 == 0:
                flush(batch_commit=True)
                print(f"{scanned} scanned ({inserted} new, {updated} updated)...")
    except FloodWaitError as e:
        print(f"Rate limited by Telegram. Sleeping for {e.seconds}s...")
        await asyncio.sleep(e.seconds)
    except AuthRestartError:
        print("Telegram requested auth restart; retrying once...")
        await client.disconnect()
        await client.connect()

    flush(batch_commit=True)

    # Mark deletions within the window (best-effort): if a message used to exist in the last N days but is not seen now
    deleted_marked = 0
    if seen_ids:
        cur.execute("DROP TABLE IF EXISTS _seen_ids")
        cur.execute("CREATE TEMP TABLE _seen_ids (message_id INTEGER PRIMARY KEY)")
        cur.executemany("INSERT OR IGNORE INTO _seen_ids(message_id) VALUES (?)", [(i,) for i in seen_ids])
        cur.execute(
            """
            UPDATE messages
            SET deleted = 1, updated_at = ?
            WHERE
                chat_identifier = ?
                AND topic_id = ?
                AND date >= ?
                AND COALESCE(deleted, 0) = 0
                AND NOT EXISTS (SELECT 1 FROM _seen_ids s WHERE s.message_id = messages.message_id)
            """,
            (run_ts, chat_identifier, topic_id, cutoff.isoformat()),
        )
        deleted_marked = cur.rowcount

    # Update checkpoint
    cur.execute(
        """
        INSERT INTO checkpoints(chat_identifier, topic_id, last_message_id, last_run_ts)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(chat_identifier, topic_id) DO UPDATE SET
            last_message_id = excluded.last_message_id,
            last_run_ts = excluded.last_run_ts
        """,
        (chat_identifier, topic_id, max_seen_id, run_ts),
    )

    conn.commit()
    conn.close()
    await client.disconnect()

    changed = inserted + updated + deleted_marked
    print(
        f"Done. New: {inserted}, Updated: {updated}, Marked deleted: {deleted_marked} "
        f"(scanned {scanned}). DB: {db_path}"
    )

    # Auto-export CSV after each successful run
    if csv_path and changed > 0:
        try:
            # For correctness with edits/deletions, do a full rebuild export.
            from export_messages import export_to_csv

            export_to_csv(db_path, csv_path)
            print(f"CSV rebuilt from DB (changes detected): {csv_path}")
        except Exception as e:
            print(f"Warning: scrape succeeded but CSV export failed: {e}")
    elif csv_path:
        print("No changes detected; CSV not changed.")

    # Auto-export ChatGPT JSONL after changes (clean, stable, includes edits)
    chatgpt_path = os.getenv("OUTPUT_CHATGPT")
    if changed > 0 and chatgpt_path:
        try:
            from export_chatgpt import export_chatgpt_jsonl, load_config as load_export_cfg

            export_chatgpt_jsonl(load_export_cfg())
        except Exception as e:
            print(f"Warning: scrape succeeded but ChatGPT export failed: {e}")


def main():
    parser = argparse.ArgumentParser(description="Scrape Telegram messages to SQLite/CSV/JSONL using Telethon.")
    parser.add_argument("--chat", help="Chat identifier: username/@username or t.me link (optionally including topic id).")
    parser.add_argument("--topic-id", type=int, help="Forum topic/thread id. Overrides topic id parsed from URL/env.")
    parser.add_argument("--days-back", type=int, help="How many days back to scrape (e.g. 365 for ~12 months).")
    parser.add_argument("--edit-lookback-days", type=int, help="How many days back to rescan for edits/deletions.")
    parser.add_argument("--output-db", help="SQLite DB file path (default from .env OUTPUT_DB).")
    parser.add_argument("--output-csv", help="CSV output path (default from .env OUTPUT_CSV).")
    parser.add_argument("--session-name", help="Telethon session name (default from .env SESSION_NAME).")

    args = parser.parse_args()
    cli = {
        "chat": args.chat,
        "topic_id": args.topic_id,
        "days_back": args.days_back,
        "edit_lookback_days": args.edit_lookback_days,
        "output_db": args.output_db,
        "output_csv": args.output_csv,
        "session_name": args.session_name,
    }
    # Remove None values so env/.env remains the fallback.
    cli = {k: v for k, v in cli.items() if v is not None}

    config = load_config(cli)
    asyncio.run(fetch_messages(config))


if __name__ == "__main__":
    main()



