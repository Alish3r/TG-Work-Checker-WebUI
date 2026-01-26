#!/usr/bin/env python3
"""
Maintenance script for TG Work Checker.
Performs various maintenance tasks to keep the system healthy.
"""
import argparse
import sys
from pathlib import Path

from logger_config import setup_logging, get_logger
from cleanup import cleanup_temp_files, cleanup_old_logs, cleanup_archived_files, vacuum_databases
from health import check_system_health, check_database_health
import glob

logger = get_logger(__name__)


def check_all_databases():
    """Check health of all databases."""
    db_files = []
    db_files.extend(glob.glob("*.db"))
    db_files.extend(glob.glob("exports/*.db"))
    db_files.extend(glob.glob("merged/*.db"))
    
    results = {}
    for db_path in db_files:
        health = check_database_health(db_path)
        results[db_path] = health
        status_icon = "✓" if health.get("status") == "healthy" else "✗"
        logger.info(f"{status_icon} {db_path}: {health.get('status', 'unknown')} - {health.get('message_count', 0)} messages")
    
    return results


def main():
    parser = argparse.ArgumentParser(description="TG Work Checker Maintenance")
    parser.add_argument("--check-dbs", action="store_true", help="Check health of all databases")
    parser.add_argument("--check-system", action="store_true", help="Check system health")
    parser.add_argument("--cleanup", action="store_true", help="Run cleanup tasks")
    parser.add_argument("--vacuum", action="store_true", help="Vacuum databases")
    parser.add_argument("--all", action="store_true", help="Run all maintenance tasks")
    
    args = parser.parse_args()
    
    setup_logging()
    logger.info("Starting maintenance tasks...")
    
    if args.all or args.check_system:
        logger.info("Checking system health...")
        system = check_system_health()
        logger.info(f"Disk free: {system['disk_free_gb']} GB / {system['disk_total_gb']} GB ({system['disk_used_percent']}% used)")
    
    if args.all or args.check_dbs:
        logger.info("Checking database health...")
        check_all_databases()
    
    if args.all or args.cleanup:
        logger.info("Running cleanup...")
        cleanup_temp_files(older_than_days=7)
        cleanup_old_logs(keep_days=30)
        cleanup_archived_files(older_than_days=90)
    
    if args.all or args.vacuum:
        logger.info("Vacuuming databases...")
        vacuum_databases()
    
    if not any([args.check_dbs, args.check_system, args.cleanup, args.vacuum, args.all]):
        parser.print_help()
        sys.exit(1)
    
    logger.info("Maintenance complete!")


if __name__ == "__main__":
    main()
