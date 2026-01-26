import argparse
import asyncio
import os
from datetime import datetime, timezone

from telethon import TelegramClient
from telethon.errors import FloodWaitError, AuthRestartError

# Reuse the same DB schema + helpers
from scrape_telegram import init_db, parse_chat_identifier, _topic_id_norm, load_config


async def backfill_full_history(config):
    api_id = config["api_id"]
    api_hash = config["api_hash"]
    session_name = config["session_name"]
    chat_identifier = config["chat_identifier"]
    topic_id = _topic_id_norm(config.get("topic_id"))
    db_path = config["output_db"]

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

    entity = await client.get_entity(chat_identifier)
    chat_id = int(getattr(entity, "id", 0)) if getattr(entity, "id", None) is not None else None

    iter_kwargs = {}
    if topic_id != -1:
        iter_kwargs["reply_to"] = topic_id

    run_ts = datetime.now(timezone.utc).isoformat()

    print("One-time backfill: scraping full history...")
    scope = f"{chat_identifier}"
    if topic_id != -1:
        scope += f" (topic/thread id: {topic_id})"
    print(f"Scope: {scope}")

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

    scanned = 0
    inserted = 0
    updated = 0
    max_seen_id = 0
    batch = []

    def flush():
        nonlocal updated
        if not batch:
            return
        before = conn.total_changes
        cur.executemany(UPSERT_SQL, batch)
        delta = conn.total_changes - before
        updated += max(0, delta - inserted)
        batch.clear()
        conn.commit()

    try:
        async for msg in client.iter_messages(entity, reverse=False, **iter_kwargs):
            if msg is None or msg.date is None:
                continue

            max_seen_id = max(max_seen_id, msg.id)

            cur.execute(
                "SELECT 1 FROM messages WHERE chat_identifier=? AND topic_id=? AND message_id=? LIMIT 1",
                (chat_identifier, topic_id, msg.id),
            )
            if cur.fetchone() is None:
                inserted += 1

            text = msg.message or ""
            is_service = 1 if msg.action is not None else 0
            edit_date = msg.edit_date.isoformat() if getattr(msg, "edit_date", None) else None

            batch.append(
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
            if scanned % 500 == 0:
                flush()
                print(f"{scanned} scanned ({inserted} new, {updated} updated)...")
    except FloodWaitError as e:
        print(f"Rate limited by Telegram. Sleeping for {e.seconds}s then continuing...")
        await asyncio.sleep(e.seconds)
    except AuthRestartError:
        print("Telegram requested auth restart; retrying once...")
        await client.disconnect()
        await client.connect()

    flush()

    # Update checkpoint to the max message id we saw
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

    print(f"Backfill complete. Scanned {scanned}. New: {inserted}, Updated: {updated}. DB: {db_path}")

    # Rebuild exports (optional)
    out_csv = config.get("output_csv")
    if out_csv:
        from export_messages import export_to_csv

        export_to_csv(db_path, out_csv)

    out_chatgpt = os.getenv("OUTPUT_CHATGPT")
    if out_chatgpt:
        from export_chatgpt import export_chatgpt_jsonl, load_config as load_export_cfg

        export_chatgpt_jsonl(load_export_cfg())


def main():
    parser = argparse.ArgumentParser(description="One-time backfill: scrape full Telegram history into DB.")
    parser.add_argument("--chat", required=False, help="Chat identifier: username/@username or t.me link.")
    parser.add_argument("--topic-id", type=int, help="Forum topic/thread id (optional).")
    parser.add_argument("--output-db", help="SQLite DB file path.")
    parser.add_argument("--output-csv", help="CSV output path.")
    parser.add_argument("--session-name", help="Telethon session name.")

    args = parser.parse_args()
    cli = {
        "chat": args.chat,
        "topic_id": args.topic_id,
        "output_db": args.output_db,
        "output_csv": args.output_csv,
        "session_name": args.session_name,
    }
    cli = {k: v for k, v in cli.items() if v is not None}

    config = load_config(cli)
    asyncio.run(backfill_full_history(config))


if __name__ == "__main__":
    main()


