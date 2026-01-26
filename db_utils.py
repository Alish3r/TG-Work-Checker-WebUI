"""
Database utility functions with connection management.
"""
import sqlite3
import contextlib
from typing import Iterator, Optional
from pathlib import Path

from logger_config import get_logger

logger = get_logger(__name__)


@contextlib.contextmanager
def get_db_connection(db_path: str, timeout: float = 30.0) -> Iterator[sqlite3.Connection]:
    """
    Context manager for database connections with proper error handling.
    
    Args:
        db_path: Path to SQLite database file
        timeout: Connection timeout in seconds
        
    Yields:
        sqlite3.Connection: Database connection
        
    Raises:
        sqlite3.Error: If connection fails
    """
    conn = None
    try:
        # Ensure directory exists
        db_file = Path(db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        
        conn = sqlite3.connect(db_path, timeout=timeout)
        conn.execute("PRAGMA foreign_keys = ON")  # Enable foreign key constraints
        conn.execute("PRAGMA journal_mode = WAL")  # Use WAL mode for better concurrency
        yield conn
        conn.commit()
    except sqlite3.Error as e:
        if conn:
            conn.rollback()
        logger.error(f"Database error in {db_path}: {e}", exc_info=True)
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Unexpected error with database {db_path}: {e}", exc_info=True)
        raise
    finally:
        if conn:
            conn.close()


def execute_safe(conn: sqlite3.Connection, query: str, params: tuple = ()) -> Optional[list]:
    """
    Execute a query safely with error handling.
    
    Args:
        conn: Database connection
        query: SQL query
        params: Query parameters
        
    Returns:
        Query results or None on error
    """
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        if query.strip().upper().startswith('SELECT'):
            return cur.fetchall()
        return None
    except sqlite3.Error as e:
        logger.error(f"Query execution failed: {query[:100]}... Error: {e}")
        raise
