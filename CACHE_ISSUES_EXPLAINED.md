# Why Code Updates Keep Getting Cached

## Root Causes

### 1. **Python Module Caching**
- Once Python imports a module, it stays in `sys.modules` in memory
- Even if you change the `.py` file, the old module object remains loaded
- Uvicorn's `--reload` tries to detect file changes, but:
  - It watches file modification times
  - Large files or rapid changes can be missed
  - Module-level code (like `get_main_ui_html()`) executes once at import time

### 2. **Multiple Server Processes**
- When you start a server, it may spawn child processes
- On Windows, processes don't always die cleanly
- Background processes can continue running even after you "stop" the server
- Multiple processes can bind to the same port (Windows allows this in some cases)
- Your browser may connect to any of these processes, getting old cached content

### 3. **Python Bytecode Cache (`.pyc` files)**
- Python compiles `.py` files to `.pyc` bytecode for faster loading
- These are stored in `__pycache__/` directories
- If the source file changes but the bytecode is newer (timestamp issue), Python might use old bytecode
- This is especially problematic with rapid edits

### 4. **Uvicorn Reload Limitations**
- `--reload` uses file watching (inotify on Linux, polling on Windows)
- Windows file watching is less reliable than Linux
- The reloader may not detect changes if:
  - Files are edited very quickly
  - The file watcher loses track of files
  - Multiple processes are modifying files

### 5. **Process Inheritance**
- When you run `uvicorn` with `--reload`, it spawns a parent process that watches files
- The parent spawns a child worker process that actually serves requests
- If the parent dies but the child doesn't, you get a "zombie" server
- These zombie processes continue serving old code

## Why It's Worse on Windows

1. **Process Management**: Windows doesn't have signals like Unix, making clean shutdowns harder
2. **File Locking**: Windows locks files more aggressively, preventing clean reloads
3. **Port Binding**: Windows allows multiple processes to bind to the same port in some cases
4. **No Process Groups**: Harder to kill process trees cleanly

## Solutions Implemented

### Improved `start_server.py`
- **Aggressive Process Killing**: Kills ALL Python processes, not just ones on the port
- **Cache Clearing**: Automatically clears `__pycache__` directories
- **Environment Variables**: Sets `PYTHONDONTWRITEBYTECODE=1` to prevent new cache files
- **Better Reload Options**: Uses `--reload-dir` and `--reload-include` for more reliable watching
- **Verification Steps**: Clear logging of what's being done

### Best Practices

1. **Always use `start_server.py`** - It handles all the cleanup automatically
2. **Kill all Python processes** before starting if you see issues:
   ```powershell
   Get-Process python | Stop-Process -Force
   ```
3. **Use different ports** if a port seems "stuck"
4. **Clear cache manually** if needed:
   ```powershell
   Get-ChildItem -Recurse -Filter __pycache__ | Remove-Item -Recurse -Force
   ```
5. **Hard refresh browser** - `Ctrl + Shift + R` to bypass browser cache

## Prevention Checklist

Before starting the server:
- [ ] Kill all Python processes: `Get-Process python | Stop-Process -Force`
- [ ] Check port is free: `netstat -ano | findstr :8001`
- [ ] Clear cache: `Get-ChildItem -Recurse -Filter __pycache__ | Remove-Item -Recurse -Force`
- [ ] Use `start_server.py` instead of running uvicorn directly
- [ ] Wait 5-10 seconds after starting before testing

## When It Still Happens

If you still see old code after using `start_server.py`:

1. **Check for zombie processes**:
   ```powershell
   netstat -ano | findstr :8001
   Get-Process | Where-Object {$_.Id -in @(PIDs_FROM_NETSTAT)}
   ```

2. **Use a completely different port**:
   ```powershell
   python start_server.py --port 9000
   ```

3. **Restart your terminal/IDE** - Sometimes the environment itself is cached

4. **Verify the code is actually saved** - Check the file timestamp

5. **Test the function directly**:
   ```powershell
   .\.venv\Scripts\python.exe -c "from web_app import get_main_ui_html; print('Footer:', '<!-- Footer -->' in get_main_ui_html())"
   ```

## Technical Details

### How Python Module Caching Works

```python
# First import
import web_app  # Module loaded into sys.modules['web_app']

# File changes on disk...

# Second import (even if file changed)
import web_app  # Returns SAME object from sys.modules, ignores disk changes!
```

### How Uvicorn Reload Works

1. Parent process watches file modification times
2. When a file changes, parent kills child worker
3. Parent spawns new child worker
4. New child imports modules fresh

**Problem**: If the parent process itself is stale, it never detects changes!

### Why Multiple Processes Happen

- You start server in terminal 1 → Process A
- You start server in terminal 2 → Process B (Process A still running!)
- You close terminal 1 → Process A becomes orphaned but keeps running
- Browser connects to Process A (old code) instead of Process B (new code)

## Long-Term Solutions

For production, consider:
- **Process managers** (systemd, supervisor, PM2)
- **Docker containers** (isolated, easy to restart)
- **CI/CD pipelines** (automated restarts)
- **Health checks** that verify code version
- **Version endpoints** that return git commit hash
