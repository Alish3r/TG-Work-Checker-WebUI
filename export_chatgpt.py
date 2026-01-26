import os
import json
import sqlite3
import re
import hashlib
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


def _parse_chat_identifier(raw_identifier: str):
    """
    Accepts `cyprusithr`, `@cyprusithr`, or `https://t.me/cyprusithr/46679`.
    Returns: (chat_identifier, topic_id_from_url_or_none)
    """
    if raw_identifier is None:
        return None, None

    identifier = raw_identifier.strip()
    topic_id = None

    if identifier.startswith("@"):
        identifier = identifier[1:]

    if identifier.startswith(("http://", "https://")):
        parsed = urlparse(identifier)
        parts = [p for p in parsed.path.split("/") if p]
        if parts:
            identifier = parts[0]
            if len(parts) >= 2:
                try:
                    topic_id = int(parts[1])
                except ValueError:
                    pass

    return identifier, topic_id


def load_config():
    if load_dotenv is not None and os.path.exists(".env"):
        load_dotenv(override=True)

    db_path = os.getenv("OUTPUT_DB", "telegram_messages.db")
    out_path = os.getenv("OUTPUT_CHATGPT", "chatgpt_export.jsonl")
    chat_identifier_raw = os.getenv("CHAT_IDENTIFIER")  # optional filter
    topic_id_raw = os.getenv("TOPIC_ID")  # optional filter
    include_deleted = os.getenv("INCLUDE_DELETED", "0") == "1"
    include_service = os.getenv("INCLUDE_SERVICE", "0") == "1"
    min_chars = int(os.getenv("MIN_CHARS", "0"))
    skip_hashtag_only = os.getenv("SKIP_HASHTAG_ONLY", "0") == "1"
    dedupe = os.getenv("DEDUPE_EXPORT", "0") == "1"
    dedupe_key = os.getenv("DEDUPE_KEY", "text")  # text | text+sender | text+sender+day
    days_back = int(os.getenv("DAYS_BACK", "30"))

    chat_identifier, topic_id_from_url = _parse_chat_identifier(chat_identifier_raw)
    topic_id = None
    if topic_id_raw not in (None, ""):
        topic_id = int(topic_id_raw)
    elif topic_id_from_url is not None:
        topic_id = topic_id_from_url

    return {
        "db_path": db_path,
        "out_path": out_path,
        "chat_identifier": chat_identifier,
        "topic_id": topic_id,
        "include_deleted": include_deleted,
        "include_service": include_service,
        "min_chars": min_chars,
        "skip_hashtag_only": skip_hashtag_only,
        "dedupe": dedupe,
        "dedupe_key": dedupe_key,
        "days_back": days_back,
    }


_ws = re.compile(r"\s+")


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # collapse excessive whitespace but keep newlines
    lines = [ _ws.sub(" ", line).strip() for line in text.split("\n") ]
    return "\n".join([ln for ln in lines if ln != ""]).strip()


def export_chatgpt_jsonl(cfg):
    if not os.path.exists(cfg["db_path"]):
        raise FileNotFoundError(f"Database not found at {cfg['db_path']}")

    conn = sqlite3.connect(cfg["db_path"])
    cur = conn.cursor()

    where = []
    params = []

    if cfg["chat_identifier"]:
        where.append("chat_identifier = ?")
        params.append(cfg["chat_identifier"])

    if cfg["topic_id"] is not None:
        where.append("COALESCE(topic_id, -1) = COALESCE(?, -1)")
        params.append(cfg["topic_id"])

    if not cfg["include_deleted"]:
        where.append("COALESCE(deleted, 0) = 0")

    if not cfg["include_service"]:
        where.append("COALESCE(is_service, 0) = 0")

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    cur.execute(
        f"""
        SELECT
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
            deleted
        FROM messages
        {where_sql}
        ORDER BY datetime(date) ASC
        """,
        params,
    )

    rows = cur.fetchall()
    conn.close()

    with open(cfg["out_path"], "w", encoding="utf-8") as f:
        seen = set()
        for (
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
        ) in rows:
            if not text:
                continue
            cleaned = clean_text(text)
            if cfg.get("min_chars", 0) and len(cleaned) < cfg["min_chars"]:
                continue
            if cfg.get("skip_hashtag_only") and cleaned and all(tok.startswith("#") for tok in cleaned.split()):
                continue

            if cfg.get("dedupe"):
                day = (date or "")[:10]  # YYYY-MM-DD from ISO
                key_parts = [cleaned]
                if cfg.get("dedupe_key") in ("text+sender", "text+sender+day"):
                    key_parts.append(sender_username or str(sender_id) or "")
                if cfg.get("dedupe_key") == "text+sender+day":
                    key_parts.append(day)
                digest = hashlib.sha256("\n".join(key_parts).encode("utf-8")).hexdigest()
                if digest in seen:
                    continue
                seen.add(digest)

            payload = {
                "chat": chat_identifier,
                "topic_id": topic_id,
                "message_id": message_id,
                "date": date,
                "edit_date": edit_date,
                "sender_id": sender_id,
                "sender_username": sender_username,
                "reply_to_msg_id": reply_to_msg_id,
                "is_service": bool(is_service),
                "deleted": bool(deleted),
                "text": cleaned,
            }
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    print(f"Wrote {len(rows)} records to {cfg['out_path']}")


def main():
    cfg = load_config()
    export_chatgpt_jsonl(cfg)


if __name__ == "__main__":
    main()


