# Troubleshooting Guide

## Issue: Server Serving Old Code / Port Conflicts

### Problem
When you update the code but the server still serves old HTML/pages, it's usually because:
1. **Multiple server processes** are running simultaneously
2. **Old processes** didn't shut down cleanly
3. **Port conflicts** prevent the new server from starting
4. **Browser cache** is serving old content

### Symptoms
- Server returns old HTML even after code changes
- "Port already in use" errors
- Multiple processes showing in `netstat` for the same port
- Code changes don't appear to take effect

### Solutions

#### 1. Use the Startup Script (Recommended)
```powershell
python start_server.py
```
This script automatically:
- Checks if the port is in use
- Kills old processes on that port
- Starts a fresh server

#### 2. Manual Cleanup
```powershell
# Find all processes using port 8000
netstat -ano | findstr :8000

# Kill all Python processes
Get-Process python | Stop-Process -Force

# Wait a few seconds, then start fresh
python -m uvicorn web_app:app --reload --port 8000
```

#### 3. Use a Different Port
```powershell
python -m uvicorn web_app:app --reload --port 8001
```

#### 4. Clear Browser Cache
- Hard refresh: `Ctrl + Shift + R` (or `Ctrl + F5`)
- Use incognito/private window
- Clear browser cache completely

### Root Causes

This issue happens because of:

1. **Python Module Caching**: Once imported, modules stay in memory even if files change
2. **Multiple Processes**: Old server processes don't die cleanly, especially on Windows
3. **Bytecode Cache**: `.pyc` files can contain stale code
4. **Uvicorn Reload Limitations**: File watching on Windows is less reliable
5. **Process Inheritance**: Parent/child process relationships can leave zombies

See `CACHE_ISSUES_EXPLAINED.md` for detailed technical explanation.

### Prevention

1. **Always use the startup script** (`start_server.py`) - it now:
   - Kills ALL Python processes (not just on the port)
   - Clears Python cache automatically
   - Sets environment variables to prevent caching
   - Uses better reload options

2. **Stop servers properly** - Use `Ctrl+C` in the terminal where the server is running

3. **If issues persist, kill all Python processes**:
   ```powershell
   Get-Process python | Stop-Process -Force
   ```

4. **Use a different port** if a port seems "stuck":
   ```powershell
   python start_server.py --port 8002
   ```

5. **Use process managers** in production (systemd, supervisor, etc.)

### How to Verify

Test if the server is serving new code:
```powershell
# Should return 404 JSON, not HTML
curl http://localhost:8000/
```

If you get HTML, old processes are still running.
