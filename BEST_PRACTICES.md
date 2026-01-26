# Best Practices & Code Standards

## Code Organization

### File Structure
```
TG Work Checker/
├── scrape_telegram.py      # Main scraping logic
├── export_messages.py       # CSV export
├── export_chatgpt.py        # JSONL export for ChatGPT
├── web_app.py              # FastAPI web server
├── config.py               # Configuration management
├── logger_config.py        # Logging setup
├── db_utils.py            # Database utilities
├── health.py              # Health checks
├── cleanup.py             # Cleanup utilities
├── maintenance.py         # Maintenance tasks
├── merge_databases.py     # Database merging
├── backfill_*.py          # Backfill scripts
└── start_server.py        # Server startup script
```

## Coding Standards

### 1. Logging
- **Always use logging** instead of print statements for production code
- Use appropriate log levels:
  - `DEBUG`: Detailed information for debugging
  - `INFO`: General informational messages
  - `WARNING`: Warning messages for potential issues
  - `ERROR`: Error messages with exception details
  - `CRITICAL`: Critical errors that may stop execution

Example:
```python
from logger_config import get_logger

logger = get_logger(__name__)

logger.info("Starting operation")
logger.error("Operation failed", exc_info=True)
```

### 2. Error Handling
- **Always catch specific exceptions** when possible
- **Log errors** with full context
- **Return meaningful error messages** to users
- **Use context managers** for resource management

Example:
```python
try:
    # operation
except SpecificError as e:
    logger.error(f"Specific error occurred: {e}", exc_info=True)
    raise HTTPException(status_code=400, detail=str(e))
except Exception as e:
    logger.error(f"Unexpected error: {e}", exc_info=True)
    raise HTTPException(status_code=500, detail="Internal server error")
```

### 3. Database Operations
- **Use connection context managers** from `db_utils.py`
- **Enable WAL mode** for better concurrency
- **Use transactions** for multi-step operations
- **Handle connection timeouts** appropriately

Example:
```python
from db_utils import get_db_connection

with get_db_connection("database.db") as conn:
    cur = conn.cursor()
    cur.execute("SELECT * FROM messages")
    results = cur.fetchall()
```

### 4. Configuration
- **Use environment variables** for configuration
- **Validate configuration** on startup
- **Provide sensible defaults** where appropriate
- **Document required variables** in `.env.example`

### 5. Type Hints
- **Add type hints** to function signatures
- **Use Optional** for nullable values
- **Document complex types** with docstrings

Example:
```python
from typing import Optional, Dict, List

def process_data(data: Dict[str, str], limit: Optional[int] = None) -> List[str]:
    """Process data and return results."""
    ...
```

### 6. Documentation
- **Add docstrings** to all functions and classes
- **Document parameters** and return values
- **Include usage examples** for complex functions

### 7. Security
- **Never commit** `.env` files or session files
- **Validate user inputs** before processing
- **Sanitize file paths** to prevent directory traversal
- **Use parameterized queries** to prevent SQL injection

### 8. Performance
- **Use batch operations** for database writes
- **Enable WAL mode** for SQLite
- **Use connection pooling** where applicable
- **Monitor resource usage** (disk, memory)

## Development Workflow

### 1. Before Committing
- Run maintenance checks: `python maintenance.py --all`
- Check for linting errors
- Verify tests pass (if applicable)
- Update documentation if needed

### 2. Code Review Checklist
- [ ] Logging added for important operations
- [ ] Error handling is comprehensive
- [ ] Type hints are present
- [ ] Docstrings are complete
- [ ] No hardcoded secrets
- [ ] Input validation is present
- [ ] Database operations use context managers

### 3. Testing
- Test with different chat types (channels, groups, topics)
- Test error scenarios (invalid inputs, network failures)
- Test edge cases (empty databases, large datasets)
- Verify cleanup operations work correctly

## Maintenance Schedule

### Daily
- Monitor logs for errors
- Check disk space usage

### Weekly
- Run database health checks
- Review and clean up temporary files
- Check for outdated dependencies

### Monthly
- Run full maintenance: `python maintenance.py --all`
- Review and update documentation
- Check for security updates
- Vacuum databases

### As Needed
- Before major operations (merges, migrations)
- After errors or issues
- When adding new features

## Performance Optimization

### Database
- Use indexes on frequently queried columns
- Vacuum databases regularly
- Use batch operations (executemany)
- Enable WAL mode

### Memory
- Process data in batches
- Close connections promptly
- Use generators for large datasets

### Network
- Handle rate limits gracefully
- Implement retry logic with exponential backoff
- Cache results when appropriate

## Security Checklist

- [ ] No secrets in code
- [ ] `.env` in `.gitignore`
- [ ] Session files in `.gitignore`
- [ ] Input validation on all endpoints
- [ ] Path sanitization for file operations
- [ ] SQL injection prevention (parameterized queries)
- [ ] Error messages don't leak sensitive information
