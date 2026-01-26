#!/usr/bin/env python3
"""
Automated backup script for databases and exports.
"""
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
import argparse

from logger_config import setup_logging, get_logger
from config import load_env_file

# Set up logging
setup_logging()
logger = get_logger(__name__)


def get_backup_dir() -> Path:
    """Get or create backup directory."""
    backup_dir = Path(os.getenv("BACKUP_DIR", "backups"))
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def backup_database(db_path: str, backup_dir: Path) -> Optional[str]:
    """Backup a single database file."""
    db_path_obj = Path(db_path)
    if not db_path_obj.exists():
        logger.warning(f"Database not found: {db_path}")
        return None
    
    # Create timestamped backup filename
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_name = f"{db_path_obj.stem}_{timestamp}.db"
    backup_path = backup_dir / backup_name
    
    try:
        # Use SQLite backup API for safe copying
        source_conn = sqlite3.connect(db_path)
        backup_conn = sqlite3.connect(str(backup_path))
        source_conn.backup(backup_conn)
        source_conn.close()
        backup_conn.close()
        
        logger.info(f"Backed up database: {db_path} -> {backup_path}")
        return str(backup_path)
    except Exception as e:
        logger.error(f"Failed to backup {db_path}: {e}", exc_info=True)
        if backup_path.exists():
            backup_path.unlink()
        return None


def backup_file(file_path: str, backup_dir: Path) -> Optional[str]:
    """Backup a single file (CSV, JSONL, etc.)."""
    file_path_obj = Path(file_path)
    if not file_path_obj.exists():
        logger.warning(f"File not found: {file_path}")
        return None
    
    # Create timestamped backup filename
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_name = f"{file_path_obj.stem}_{timestamp}{file_path_obj.suffix}"
    backup_path = backup_dir / backup_name
    
    try:
        shutil.copy2(file_path, backup_path)
        logger.info(f"Backed up file: {file_path} -> {backup_path}")
        return str(backup_path)
    except Exception as e:
        logger.error(f"Failed to backup {file_path}: {e}", exc_info=True)
        return None


def find_databases() -> List[str]:
    """Find all .db files in current directory."""
    databases = []
    for db_file in Path(".").glob("*.db"):
        # Skip backup databases
        if "backup" not in db_file.name.lower() and "archive" not in db_file.name.lower():
            databases.append(str(db_file))
    return databases


def cleanup_old_backups(backup_dir: Path, days_to_keep: int = 30) -> int:
    """Remove backups older than specified days."""
    cutoff_time = datetime.now(timezone.utc).timestamp() - (days_to_keep * 24 * 60 * 60)
    deleted = 0
    
    for backup_file in backup_dir.glob("*"):
        if backup_file.is_file():
            try:
                if backup_file.stat().st_mtime < cutoff_time:
                    backup_file.unlink()
                    deleted += 1
                    logger.debug(f"Deleted old backup: {backup_file}")
            except Exception as e:
                logger.warning(f"Failed to delete {backup_file}: {e}")
    
    if deleted > 0:
        logger.info(f"Cleaned up {deleted} old backup(s)")
    
    return deleted


def main():
    """Main backup function."""
    parser = argparse.ArgumentParser(description="Backup databases and exports")
    parser.add_argument("--db", help="Specific database to backup")
    parser.add_argument("--all-dbs", action="store_true", help="Backup all databases")
    parser.add_argument("--csv", help="CSV file to backup")
    parser.add_argument("--jsonl", help="JSONL file to backup")
    parser.add_argument("--cleanup", type=int, metavar="DAYS", help="Clean up backups older than N days")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be backed up without doing it")
    
    args = parser.parse_args()
    
    load_env_file()
    backup_dir = get_backup_dir()
    
    backed_up = []
    
    if args.cleanup:
        if not args.dry_run:
            cleanup_old_backups(backup_dir, args.cleanup)
        else:
            logger.info(f"[DRY RUN] Would clean up backups older than {args.cleanup} days")
    
    if args.db:
        if not args.dry_run:
            result = backup_database(args.db, backup_dir)
            if result:
                backed_up.append(result)
        else:
            logger.info(f"[DRY RUN] Would backup: {args.db}")
    
    if args.all_dbs:
        databases = find_databases()
        for db in databases:
            if not args.dry_run:
                result = backup_database(db, backup_dir)
                if result:
                    backed_up.append(result)
            else:
                logger.info(f"[DRY RUN] Would backup: {db}")
    
    if args.csv:
        if not args.dry_run:
            result = backup_file(args.csv, backup_dir)
            if result:
                backed_up.append(result)
        else:
            logger.info(f"[DRY RUN] Would backup: {args.csv}")
    
    if args.jsonl:
        if not args.dry_run:
            result = backup_file(args.jsonl, backup_dir)
            if result:
                backed_up.append(result)
        else:
            logger.info(f"[DRY RUN] Would backup: {args.jsonl}")
    
    if not any([args.db, args.all_dbs, args.csv, args.jsonl, args.cleanup]):
        # Default: backup all databases
        databases = find_databases()
        if databases:
            logger.info(f"Backing up {len(databases)} database(s)...")
            for db in databases:
                if not args.dry_run:
                    result = backup_database(db, backup_dir)
                    if result:
                        backed_up.append(result)
                else:
                    logger.info(f"[DRY RUN] Would backup: {db}")
        else:
            logger.warning("No databases found to backup")
    
    if backed_up:
        logger.info(f"Backup complete. {len(backed_up)} file(s) backed up to {backup_dir}")
    elif not args.dry_run:
        logger.info("No files were backed up")


if __name__ == "__main__":
    main()
