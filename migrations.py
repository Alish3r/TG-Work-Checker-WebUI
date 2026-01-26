#!/usr/bin/env python3
"""
Database migration system for schema evolution.
"""
import sqlite3
from typing import List, Dict, Any, Optional
from pathlib import Path

from logger_config import get_logger

logger = get_logger(__name__)


class Migration:
    """Represents a single database migration."""
    
    def __init__(self, version: int, name: str, up: str, down: Optional[str] = None):
        self.version = version
        self.name = name
        self.up = up  # SQL to apply migration
        self.down = down  # SQL to rollback migration (optional)
    
    def apply(self, conn: sqlite3.Connection) -> None:
        """Apply this migration."""
        logger.info(f"Applying migration {self.version}: {self.name}")
        conn.executescript(self.up)
        conn.commit()
    
    def rollback(self, conn: sqlite3.Connection) -> None:
        """Rollback this migration."""
        if not self.down:
            raise ValueError(f"Migration {self.version} does not support rollback")
        logger.info(f"Rolling back migration {self.version}: {self.name}")
        conn.executescript(self.down)
        conn.commit()


# Define all migrations in order
MIGRATIONS: List[Migration] = [
    Migration(
        version=1,
        name="add_topic_id_column",
        up="""
        -- Add topic_id column if it doesn't exist
        ALTER TABLE messages ADD COLUMN topic_id INTEGER NOT NULL DEFAULT -1;
        """,
        down="""
        -- Note: SQLite doesn't support dropping columns easily
        -- This would require table rebuild
        """
    ),
    Migration(
        version=2,
        name="add_edit_date_column",
        up="""
        -- Add edit_date column if it doesn't exist
        ALTER TABLE messages ADD COLUMN edit_date TEXT;
        """,
    ),
    Migration(
        version=3,
        name="add_deleted_column",
        up="""
        -- Add deleted column if it doesn't exist
        ALTER TABLE messages ADD COLUMN deleted INTEGER DEFAULT 0;
        """,
    ),
    Migration(
        version=4,
        name="add_updated_at_column",
        up="""
        -- Add updated_at column if it doesn't exist
        ALTER TABLE messages ADD COLUMN updated_at TEXT;
        """,
    ),
]


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Get current schema version from database."""
    cur = conn.cursor()
    try:
        # Check if migrations table exists
        cur.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='schema_migrations'
        """)
        if not cur.fetchone():
            # Create migrations table
            cur.execute("""
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
            """)
            conn.commit()
            return 0
        
        # Get latest version
        cur.execute("SELECT MAX(version) FROM schema_migrations")
        result = cur.fetchone()
        return result[0] if result[0] is not None else 0
    except Exception as e:
        logger.error(f"Error getting schema version: {e}", exc_info=True)
        return 0


def record_migration(conn: sqlite3.Connection, migration: Migration) -> None:
    """Record that a migration was applied."""
    from datetime import datetime, timezone
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO schema_migrations (version, name, applied_at)
        VALUES (?, ?, ?)
    """, (migration.version, migration.name, datetime.now(timezone.utc).isoformat()))
    conn.commit()


def migrate_database(db_path: str, target_version: Optional[int] = None) -> None:
    """
    Apply pending migrations to a database.
    
    Args:
        db_path: Path to database file
        target_version: Target version (None = apply all pending)
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    
    try:
        current_version = get_schema_version(conn)
        target = target_version if target_version is not None else len(MIGRATIONS)
        
        if current_version >= target:
            logger.info(f"Database {db_path} is already at version {current_version} (target: {target})")
            return
        
        # Apply pending migrations
        for migration in MIGRATIONS:
            if migration.version > current_version and migration.version <= target:
                migration.apply(conn)
                record_migration(conn, migration)
                logger.info(f"Migration {migration.version} applied successfully")
        
        logger.info(f"Database {db_path} migrated to version {target}")
    except Exception as e:
        logger.error(f"Migration failed: {e}", exc_info=True)
        conn.rollback()
        raise
    finally:
        conn.close()


def check_migrations(db_path: str) -> Dict[str, Any]:
    """Check migration status of a database."""
    conn = sqlite3.connect(db_path)
    try:
        current_version = get_schema_version(conn)
        latest_version = len(MIGRATIONS)
        pending = [m for m in MIGRATIONS if m.version > current_version]
        
        return {
            "current_version": current_version,
            "latest_version": latest_version,
            "pending_count": len(pending),
            "pending_migrations": [{"version": m.version, "name": m.name} for m in pending],
            "up_to_date": current_version >= latest_version
        }
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Database migration tool")
    parser.add_argument("db_path", help="Path to database file")
    parser.add_argument("--check", action="store_true", help="Check migration status")
    parser.add_argument("--version", type=int, help="Target version (default: latest)")
    
    args = parser.parse_args()
    
    if args.check:
        status = check_migrations(args.db_path)
        print(f"Current version: {status['current_version']}")
        print(f"Latest version: {status['latest_version']}")
        print(f"Up to date: {status['up_to_date']}")
        if status['pending_count'] > 0:
            print(f"Pending migrations: {status['pending_count']}")
            for m in status['pending_migrations']:
                print(f"  - {m['version']}: {m['name']}")
    else:
        migrate_database(args.db_path, args.version)
