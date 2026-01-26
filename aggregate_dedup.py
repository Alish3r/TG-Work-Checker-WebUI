import argparse
import hashlib
import os
import sqlite3


def normalize_text(text: str) -> str:
    return " ".join((text or "").split()).strip()


def make_hash(text: str, sender_id, sender_username: str, date_iso: str, dedupe_key: str) -> str:
    cleaned = normalize_text(text)
    day = (date_iso or "")[:10]
    key_parts = [cleaned]
    if dedupe_key in ("text+sender", "text+sender+day"):
        key_parts.append((sender_username or "") or (str(sender_id) if sender_id is not None else ""))
    if dedupe_key == "text+sender+day":
        key_parts.append(day)
    return hashlib.sha256("\n".join(key_parts).encode("utf-8")).hexdigest()


def init_agg_db(path: str):
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dedupe_hash TEXT NOT NULL UNIQUE,
            text TEXT NOT NULL,
            first_date TEXT,
            last_date TEXT,
            sender_id INTEGER,
            sender_username TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL UNIQUE,
            source_db_path TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS post_sources (
            post_id INTEGER NOT NULL,
            source_id INTEGER NOT NULL,
            chat_identifier TEXT,
            topic_id INTEGER,
            message_id INTEGER,
            date TEXT,
            PRIMARY KEY (post_id, source_id, chat_identifier, topic_id, message_id),
            FOREIGN KEY (post_id) REFERENCES posts(id),
            FOREIGN KEY (source_id) REFERENCES sources(id)
        )
        """
    )
    conn.commit()
    return conn


def load_messages(db_path: str):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # tolerate older schemas by selecting only known columns if present
    cur.execute("PRAGMA table_info(messages)")
    cols = {r[1] for r in cur.fetchall()}

    def col(name, fallback):
        return name if name in cols else fallback

    q = f"""
    SELECT
        {col('chat_identifier','NULL')},
        {col('topic_id','-1')},
        {col('message_id','NULL')},
        {col('date','NULL')},
        {col('sender_id','NULL')},
        {col('sender_username','NULL')},
        {col('text','NULL')},
        {col('deleted','0')}
    FROM messages
    """
    cur.execute(q)
    rows = cur.fetchall()
    conn.close()
    return rows


def get_or_create_source(cur, source_name: str, source_db_path: str) -> int:
    cur.execute("INSERT OR IGNORE INTO sources(source_name, source_db_path) VALUES (?, ?)", (source_name, source_db_path))
    cur.execute("SELECT id FROM sources WHERE source_name=?", (source_name,))
    return int(cur.fetchone()[0])


def upsert_post(cur, dedupe_hash: str, text: str, date_iso: str, sender_id, sender_username):
    # Insert new post or update last_date if later
    cur.execute(
        """
        INSERT INTO posts(dedupe_hash, text, first_date, last_date, sender_id, sender_username)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(dedupe_hash) DO UPDATE SET
            last_date = CASE
                WHEN excluded.last_date > posts.last_date THEN excluded.last_date
                ELSE posts.last_date
            END
        """,
        (dedupe_hash, text, date_iso, date_iso, sender_id, sender_username),
    )
    cur.execute("SELECT id FROM posts WHERE dedupe_hash=?", (dedupe_hash,))
    return int(cur.fetchone()[0])


def main():
    parser = argparse.ArgumentParser(description="Aggregate multiple Telegram scrape DBs into one deduplicated DB.")
    parser.add_argument("--out-db", required=True, help="Output aggregated SQLite DB path.")
    parser.add_argument("--dedupe-key", default="text", choices=["text", "text+sender", "text+sender+day"])
    parser.add_argument("--include-deleted", action="store_true", help="Include messages marked deleted in source DBs.")
    parser.add_argument("--source", action="append", nargs=2, metavar=("NAME", "DB_PATH"), required=True, help="Add a source: NAME DB_PATH")
    args = parser.parse_args()

    out_db = args.out_db
    conn = init_agg_db(out_db)
    cur = conn.cursor()

    total_in = 0
    total_kept = 0

    for source_name, db_path in args.source:
        db_path = os.path.abspath(db_path)
        print(f"Loading source {source_name} from {db_path}")
        source_id = get_or_create_source(cur, source_name, db_path)

        for (chat_identifier, topic_id, message_id, date_iso, sender_id, sender_username, text, deleted) in load_messages(db_path):
            total_in += 1
            if not args.include_deleted and int(deleted or 0) == 1:
                continue
            if not text:
                continue
            cleaned = normalize_text(text)
            if not cleaned:
                continue

            h = make_hash(cleaned, sender_id, sender_username or "", date_iso or "", args.dedupe_key)
            post_id = upsert_post(cur, h, cleaned, date_iso, sender_id, sender_username)
            cur.execute(
                """
                INSERT OR IGNORE INTO post_sources(
                    post_id, source_id, chat_identifier, topic_id, message_id, date
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (post_id, source_id, chat_identifier, int(topic_id) if topic_id is not None else -1, message_id, date_iso),
            )
            total_kept += 1

        conn.commit()

    print(f"Done. Source rows read: {total_in}. Rows kept (pre-dedupe filtering): {total_kept}.")
    cur.execute("SELECT COUNT(*) FROM posts")
    print(f"Unique posts in aggregated DB: {cur.fetchone()[0]}")
    conn.close()


if __name__ == "__main__":
    main()


