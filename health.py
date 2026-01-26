"""
Health check and monitoring utilities.
"""
import sqlite3
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any


def check_database_health(db_path: str) -> Dict[str, Any]:
    """Check database health and return status."""
    if not os.path.exists(db_path):
        return {
            "status": "error",
            "message": f"Database file not found: {db_path}",
            "exists": False
        }
    
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        
        # Check if messages table exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
        has_table = cur.fetchone() is not None
        
        if not has_table:
            conn.close()
            return {
                "status": "error",
                "message": "Messages table not found",
                "exists": True,
                "has_table": False
            }
        
        # Get basic stats
        cur.execute("SELECT COUNT(*) FROM messages")
        count = cur.fetchone()[0]
        
        cur.execute("SELECT MIN(date), MAX(date) FROM messages WHERE date IS NOT NULL")
        date_range = cur.fetchone()
        
        # Check database integrity
        cur.execute("PRAGMA integrity_check")
        integrity = cur.fetchone()[0]
        
        # Get file size
        file_size = os.path.getsize(db_path)
        
        conn.close()
        
        return {
            "status": "healthy" if integrity == "ok" else "warning",
            "exists": True,
            "has_table": True,
            "message_count": count,
            "earliest_date": date_range[0] if date_range and date_range[0] else None,
            "latest_date": date_range[1] if date_range and date_range[1] else None,
            "integrity": integrity,
            "file_size_mb": round(file_size / (1024 * 1024), 2),
            "last_checked": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "exists": True
        }


def check_system_health() -> Dict[str, Any]:
    """Check system health (disk space, etc.)."""
    import shutil
    
    disk = shutil.disk_usage(".")
    
    return {
        "disk_free_gb": round(disk.free / (1024**3), 2),
        "disk_total_gb": round(disk.total / (1024**3), 2),
        "disk_used_percent": round((disk.used / disk.total) * 100, 2),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
