# Maintenance Guide

This document outlines best practices and maintenance procedures for TG Work Checker.

## Regular Maintenance Tasks

### 1. Database Health Checks

Check the health of all databases:
```powershell
python maintenance.py --check-dbs
```

This will:
- Verify database integrity
- Check message counts
- Report any issues

### 2. System Health Monitoring

Check system resources:
```powershell
python maintenance.py --check-system
```

### 3. Cleanup Tasks

Run cleanup to remove old files:
```powershell
python maintenance.py --cleanup
```

Or use the dedicated cleanup script:
```powershell
python cleanup.py --temp-days 7 --log-days 30 --archive-days 90
```

### 4. Database Optimization

Vacuum databases to reclaim space:
```powershell
python maintenance.py --vacuum
```

### 5. Run All Maintenance

Run all maintenance tasks at once:
```powershell
python maintenance.py --all
```

## Best Practices

### Logging

- Logs are stored in `logs/` directory
- Log level can be set via `LOG_LEVEL` environment variable (DEBUG, INFO, WARNING, ERROR)
- Logs are rotated daily
- Old logs are automatically cleaned up after 30 days

### Error Handling

- All database operations use connection context managers
- Errors are logged with full stack traces
- API endpoints return appropriate HTTP status codes

### Configuration

- Use `.env` file for configuration (see `.env.example`)
- Validate configuration on startup
- Required variables: `API_ID`, `API_HASH`

### Database Management

- Use WAL mode for better concurrency
- Regular vacuuming to reclaim space
- Check integrity periodically
- Backup important databases before major operations

### Security

- Never commit `.env` files
- Keep session files secure (they're in `.gitignore`)
- Use environment variables for sensitive data
- Validate all user inputs

### Performance

- Use batch operations for database writes
- Enable WAL mode for SQLite
- Use connection pooling where possible
- Monitor disk space usage

## Health Check Endpoints

The web API provides health check endpoints:

- `GET /health` - Overall system health
- `GET /health/database/{db_name}` - Specific database health

## Monitoring

Set up monitoring to:
- Check `/health` endpoint regularly
- Monitor disk space usage
- Watch for error logs
- Track database sizes

## Backup Strategy

1. **Regular backups** of important databases
2. **Before major operations** (merges, cleanups)
3. **Version control** for configuration (not secrets)
4. **Archive old data** before deletion

## Troubleshooting

See `TROUBLESHOOTING.md` for common issues and solutions.
