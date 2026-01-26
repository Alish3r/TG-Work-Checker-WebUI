import os
import csv
import sqlite3

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

CSV_HEADERS = [
    "chat_id",
    "chat_identifier",
    "topic_id",
    "message_id",
    "date",
    "edit_date",
    "sender_id",
    "sender_username",
    "text",
    "reply_to_msg_id",
    "is_service",
    "deleted",
    "updated_at",
]


def load_config():
    if load_dotenv is not None and os.path.exists(".env"):
        load_dotenv()

    db_path = os.getenv("OUTPUT_DB", "telegram_messages.db")
    csv_path = os.getenv("OUTPUT_CSV", "telegram_messages.csv")
    dedupe = os.getenv("DEDUPE_EXPORT", "0") == "1"
    dedupe_key = os.getenv("DEDUPE_KEY", "text")
    return {"db_path": db_path, "csv_path": csv_path, "dedupe": dedupe, "dedupe_key": dedupe_key}


def ensure_csv_exists_with_header(csv_path: str):
    """
    Create a new CSV with an Excel-friendly UTF-8 BOM + header row.
    If the CSV already exists, do nothing.
    """
    if os.path.exists(csv_path):
        return

    # utf-8-sig includes a BOM so Excel opens Cyrillic correctly
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADERS)


def append_rows_to_csv(csv_path: str, rows: list[tuple]):
    """
    Append rows to an existing CSV. If the file doesn't exist yet, create it first.
    Note: when appending, use plain utf-8 (the BOM only needs to exist once at the start).
    """
    ensure_csv_exists_with_header(csv_path)
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows)


def export_to_csv(db_path: str, csv_path: str, dedupe: bool = False, dedupe_key: str = "text"):
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found at {db_path}")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT chat_id, chat_identifier, topic_id, message_id, date, edit_date,
               sender_id, sender_username, text, reply_to_msg_id, is_service, deleted, updated_at
        FROM messages
        ORDER BY datetime(date) ASC
        """
    )

    rows = cur.fetchall()
    # Use utf-8-sig to include BOM so Excel opens Cyrillic correctly
    # Optional dedupe in export (keeps first occurrence chronologically)
    if dedupe:
        import hashlib
        seen = set()
        deduped = []
        for r in rows:
            # r fields per SELECT in this file:
            # chat_id, chat_identifier, topic_id, message_id, date, edit_date,
            # sender_id, sender_username, text, reply_to_msg_id, is_service, deleted, updated_at
            date = r[4] or ""
            day = date[:10]
            sender_id = r[6]
            sender_username = r[7] or ""
            text = r[8] or ""
            cleaned = " ".join(text.split())
            key_parts = [cleaned]
            if dedupe_key in ("text+sender", "text+sender+day"):
                key_parts.append(sender_username or str(sender_id) or "")
            if dedupe_key == "text+sender+day":
                key_parts.append(day)
            digest = hashlib.sha256("\n".join(key_parts).encode("utf-8")).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            deduped.append(r)
        rows = deduped

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADERS)
        writer.writerows(rows)

    conn.close()
    print(f"Wrote {len(rows)} rows to {csv_path}")


def main():
    cfg = load_config()
    export_to_csv(cfg["db_path"], cfg["csv_path"], cfg.get("dedupe", False), cfg.get("dedupe_key", "text"))


if __name__ == "__main__":
    main()

