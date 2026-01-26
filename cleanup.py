#!/usr/bin/env python3
"""
Cleanup script to remove temporary files and old data.
"""
import os
import glob
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta, timezone
import argparse

from logger_config import setup_logging, get_logger

logger = get_logger(__name__)


def cleanup_temp_files(older_than_days: int = 7):
    """Remove temporary database files older than specified days."""
    patterns = [
        "temp_*.db",
        "check_*.py",
        "*.tmp"
    ]
    
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    removed = 0
    
    for pattern in patterns:
        for filepath in glob.glob(pattern):
            try:
                file_time = datetime.fromtimestamp(os.path.getmtime(filepath), tz=timezone.utc)
                if file_time < cutoff:
                    os.remove(filepath)
                    logger.info(f"Removed temporary file: {filepath}")
                    removed += 1
            except Exception as e:
                logger.warning(f"Could not remove {filepath}: {e}")
    
    return removed


def cleanup_old_logs(keep_days: int = 30):
    """Remove log files older than specified days."""
    log_dir = Path("logs")
    if not log_dir.exists():
        return 0
    
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    removed = 0
    
    for log_file in log_dir.glob("*.log"):
        try:
            file_time = datetime.fromtimestamp(log_file.stat().st_mtime, tz=timezone.utc)
            if file_time < cutoff:
                log_file.unlink()
                logger.info(f"Removed old log: {log_file}")
                removed += 1
        except Exception as e:
            logger.warning(f"Could not remove {log_file}: {e}")
    
    return removed


def cleanup_archived_files(older_than_days: int = 90):
    """Remove archived files older than specified days."""
    archive_dir = Path("archived")
    if not archive_dir.exists():
        return 0
    
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    removed = 0
    
    for file_path in archive_dir.glob("*"):
        if not file_path.is_file():
            continue
        
        try:
            # Try to extract timestamp from filename
            import re
            match = re.search(r"_(\d{8}_\d{6})", file_path.name)
            if match:
                timestamp_str = match.group(1)
                file_date = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                file_date = file_date.replace(tzinfo=timezone.utc)
            else:
                # Use file modification time
                file_date = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
            
            if file_date < cutoff:
                file_path.unlink()
                logger.info(f"Removed archived file: {file_path}")
                removed += 1
        except Exception as e:
            logger.warning(f"Could not remove {file_path}: {e}")
    
    return removed


def vacuum_databases():
    """Vacuum SQLite databases to reclaim space."""
    db_files = []
    db_files.extend(glob.glob("*.db"))
    db_files.extend(glob.glob("exports/*.db"))
    db_files.extend(glob.glob("merged/*.db"))
    
    vacuumed = 0
    for db_path in db_files:
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("VACUUM")
            conn.close()
            logger.info(f"Vacuumed database: {db_path}")
            vacuumed += 1
        except Exception as e:
            logger.warning(f"Could not vacuum {db_path}: {e}")
    
    return vacuumed


def main():
    parser = argparse.ArgumentParser(description="Cleanup temporary files and optimize databases")
    parser.add_argument("--temp-days", type=int, default=7, help="Remove temp files older than N days")
    parser.add_argument("--log-days", type=int, default=30, help="Keep logs for N days")
    parser.add_argument("--archive-days", type=int, default=90, help="Remove archived files older than N days")
    parser.add_argument("--vacuum", action="store_true", help="Vacuum SQLite databases")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be removed without actually removing")
    
    args = parser.parse_args()
    
    setup_logging()
    
    if args.dry_run:
        logger.info("DRY RUN MODE - No files will be removed")
    
    results = {
        "temp_files": 0,
        "log_files": 0,
        "archived_files": 0,
        "databases_vacuumed": 0
    }
    
    if not args.dry_run:
        results["temp_files"] = cleanup_temp_files(args.temp_days)
        results["log_files"] = cleanup_old_logs(args.log_days)
        results["archived_files"] = cleanup_archived_files(args.archive_days)
        
        if args.vacuum:
            results["databases_vacuumed"] = vacuum_databases()
    else:
        # Dry run - just count
        logger.info("Would clean up:")
        logger.info(f"  - Temp files older than {args.temp_days} days")
        logger.info(f"  - Log files older than {args.log_days} days")
        logger.info(f"  - Archived files older than {args.archive_days} days")
        if args.vacuum:
            logger.info("  - Would vacuum all databases")
    
    logger.info("Cleanup complete:")
    for key, value in results.items():
        logger.info(f"  {key}: {value}")
    
    return results


if __name__ == "__main__":
    main()
