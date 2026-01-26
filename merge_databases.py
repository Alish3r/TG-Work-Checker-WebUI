import argparse
import os
import sqlite3
import glob
import re
from pathlib import Path


def get_chat_info(db_path: str):
    """Get chat identifier and topic_id from a database."""
    if not os.path.exists(db_path):
        return None
    
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT chat_identifier, topic_id FROM messages LIMIT 1")
        row = cur.fetchone()
        conn.close()
        
        if row:
            return {"chat_identifier": row[0], "topic_id": row[1] if row[1] != -1 else None}
    except Exception:
        pass
    return None


def merge_databases(source_dbs: list[str], output_db: str):
    """Merge multiple databases into one, deduplicating by (chat_identifier, topic_id, message_id)."""
    if not source_dbs:
        print("No source databases provided")
        return
    
    print(f"Merging {len(source_dbs)} databases into {output_db}...")
    
    # Initialize output DB
    from scrape_telegram import init_db
    conn = init_db(output_db)
    cur = conn.cursor()
    
    total_inserted = 0
    total_skipped = 0
    
    UPSERT_SQL = """
    INSERT INTO messages (
        chat_id, chat_identifier, topic_id, message_id, date, edit_date,
        sender_id, sender_username, text, reply_to_msg_id, is_service, deleted, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    
    for source_db in source_dbs:
        if not os.path.exists(source_db):
            print(f"  Skipping {source_db} (not found)")
            continue
        
        print(f"  Processing {source_db}...")
        try:
            src_conn = sqlite3.connect(source_db)
            src_cur = src_conn.cursor()
            
            # Check if messages table exists
            src_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
            if not src_cur.fetchone():
                print(f"    No messages table in {source_db}, skipping")
                src_conn.close()
                continue
            
            # Get all messages
            src_cur.execute("""
                SELECT chat_id, chat_identifier, topic_id, message_id, date, edit_date,
                       sender_id, sender_username, text, reply_to_msg_id, is_service, deleted, updated_at
                FROM messages
            """)
            
            batch = []
            batch_count = 0
            
            for row in src_cur.fetchall():
                batch.append(row)
                batch_count += 1
                
                if len(batch) >= 500:
                    before = conn.total_changes
                    cur.executemany(UPSERT_SQL, batch)
                    inserted = conn.total_changes - before
                    total_inserted += inserted
                    total_skipped += len(batch) - inserted
                    batch.clear()
                    conn.commit()
            
            # Flush remaining
            if batch:
                before = conn.total_changes
                cur.executemany(UPSERT_SQL, batch)
                inserted = conn.total_changes - before
                total_inserted += inserted
                total_skipped += len(batch) - inserted
                batch.clear()
                conn.commit()
            
            src_conn.close()
            print(f"    Processed {batch_count} messages from {source_db}")
        except Exception as e:
            print(f"    Error processing {source_db}: {e}")
    
    conn.close()
    print(f"\nMerge complete!")
    print(f"  Total messages processed: {total_inserted + total_skipped}")
    print(f"  New messages inserted: {total_inserted}")
    print(f"  Duplicates skipped: {total_skipped}")
    print(f"  Output: {output_db}")


def main():
    parser = argparse.ArgumentParser(description="Merge multiple Telegram scrape databases by chat identifier.")
    parser.add_argument("--auto", action="store_true", help="Auto-detect and merge databases by chat identifier")
    parser.add_argument("--output-dir", default="merged", help="Output directory for merged databases")
    parser.add_argument("--dbs", nargs="+", help="Specific database files to merge (if not using --auto)")
    parser.add_argument("--output-name", help="Output database name (if not using --auto)")
    
    args = parser.parse_args()
    
    if args.auto:
        # Find all .db files
        dbs = []
        for pattern in ["*.db", "exports/*.db"]:
            dbs.extend(glob.glob(pattern))
        
        # Group by chat_identifier only (ignore topic_id to merge all topics for same chat)
        by_chat = {}
        for db in dbs:
            info = get_chat_info(db)
            if info:
                # Group by chat_identifier only, not topic_id
                key = info["chat_identifier"]
                if key not in by_chat:
                    by_chat[key] = []
                by_chat[key].append(db)
        
        # Create output directory
        os.makedirs(args.output_dir, exist_ok=True)
        
        # Merge each group
        for chat_id, db_list in by_chat.items():
            if len(db_list) <= 1:
                print(f"Skipping {chat_id} (only 1 database)")
                continue
            
            # Generate output name (just chat identifier, no topic_id since we're merging all topics)
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", chat_id)
            output_db = os.path.join(args.output_dir, f"{safe_name}.db")
            
            print(f"\nMerging {len(db_list)} databases for chat: {chat_id}...")
            print(f"  Databases: {', '.join([os.path.basename(db) for db in db_list])}")
            merge_databases(db_list, output_db)
    else:
        if not args.dbs:
            parser.error("Either --auto or --dbs must be specified")
        
        output_db = args.output_name or "merged.db"
        merge_databases(args.dbs, output_db)


if __name__ == "__main__":
    main()
