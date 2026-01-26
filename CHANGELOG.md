# Changelog

## [0.3.0] - 2026-01-25

### Added
- Comprehensive logging system with daily log rotation
- Health check endpoints (`/health`, `/health/database/{db_name}`)
- Configuration management with validation (`config.py`)
- Database utilities with connection management (`db_utils.py`)
- Maintenance scripts (`maintenance.py`, `cleanup.py`)
- Server startup script to prevent port conflicts (`start_server.py`)
- Archive system for deleted files (3-month retention)
- Documentation:
  - `MAINTENANCE.md` - Maintenance procedures
  - `BEST_PRACTICES.md` - Coding standards
  - `TROUBLESHOOTING.md` - Common issues
  - `MAINTENANCE_SUMMARY.md` - What was done
- Code quality tools (pre-commit hooks, black, isort, flake8)
- `.env.example` template
- Improved `.gitignore`

### Improved
- Error handling throughout the codebase
- Logging added to all major functions
- Database connection management
- Input validation and sanitization
- Error messages with better context

### Changed
- Web interface removed (dashboard and scraper pages)
- Only API endpoints remain
- Server startup process improved

### Fixed
- Port conflict issues with startup script
- Database error handling
- Configuration loading (BOM handling)

## [0.2.0] - Previous versions
- Initial web interface
- Basic scraping functionality
- CSV and JSONL exports
- Database merging and deduplication
