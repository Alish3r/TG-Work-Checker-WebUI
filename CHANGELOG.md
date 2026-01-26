# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-01-25

### Added

#### Logging & Monitoring
- **Comprehensive logging system** (`logger_config.py`)
  - Centralized logging configuration
  - Daily log rotation in `logs/` directory
  - Configurable log levels via `LOG_LEVEL` environment variable
  - Structured logging with timestamps and context
  - Automatic suppression of noisy library logs

- **Health monitoring** (`health.py`)
  - System health endpoint (`GET /health`)
  - Database health checks (`GET /health/database/{db_name}`)
  - Disk space monitoring
  - Database integrity checking

#### Configuration & Utilities
- **Configuration management** (`config.py`)
  - Robust environment variable handling
  - BOM handling for PowerShell-created `.env` files
  - Type-safe configuration access
  - Validation functions for required/optional settings

- **Database utilities** (`db_utils.py`)
  - Connection context managers
  - Automatic WAL mode enablement
  - Proper error handling and rollback
  - Safe query execution helpers

#### Maintenance & Automation
- **Maintenance scripts**
  - `maintenance.py` - Comprehensive maintenance tasks
  - `cleanup.py` - Automated file cleanup
  - `start_server.py` - Server startup with port conflict prevention

- **Archive system**
  - Deleted files archived for 3 months
  - Automatic cleanup of old archives (90+ days)
  - Timestamp-based file organization

#### Documentation
- `MAINTENANCE.md` - Maintenance procedures and schedules
- `BEST_PRACTICES.md` - Coding standards and guidelines
- `TROUBLESHOOTING.md` - Common issues and solutions
- `MAINTENANCE_SUMMARY.md` - Summary of improvements
- `.env.example` - Configuration template

#### Code Quality
- Pre-commit hooks configuration (`.pre-commit-config.yaml`)
- Tool configuration (`pyproject.toml`)
- Support for black, isort, flake8, mypy
- Improved `.gitignore` with comprehensive patterns

### Improved

- **Error Handling**
  - Better error messages with full context
  - Proper exception logging with stack traces
  - User-friendly error responses
  - Input validation and sanitization throughout

- **Database Operations**
  - Connection management with context managers
  - Automatic WAL mode for better concurrency
  - Improved error handling and rollback
  - Better handling of schema migrations

- **Server Management**
  - Automatic cleanup of old processes
  - Port conflict detection and resolution
  - Better process lifecycle management

- **Logging**
  - Added logging to all major functions
  - Structured log messages
  - Configurable verbosity levels

### Changed

- **Web Interface**
  - Removed HTML dashboard and scraper pages
  - Now API-only (REST endpoints)
  - Improved API documentation

- **Server Startup**
  - New startup script prevents port conflicts
  - Automatic process cleanup
  - Better error messages

### Fixed

- Port conflict issues with multiple server instances
- Database error handling for edge cases
- Configuration loading (BOM handling for PowerShell)
- Session file management
- Database schema migration handling

### Security

- Improved input validation
- Path sanitization for file operations
- Better error messages (no sensitive data leakage)
- Session file protection

## [0.2.0] - Previous versions

### Features
- Initial web interface (dashboard and scraper pages)
- Basic scraping functionality
- CSV and JSONL exports
- Database merging and deduplication
- Forum topic/thread support
- Incremental scraping with checkpoints
- Edit and deletion tracking
- Command-line argument support
- Multiple database management

## [0.1.0] - Initial Release

### Features
- Basic Telegram message scraping
- SQLite storage
- CSV export
- Support for channels and groups
