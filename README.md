# TG Work Checker

This project scrapes messages from a specific Telegram chat (including **forum topic/thread links**) using **Telethon**, stores them in a local **SQLite** database, and produces:

- an **Excel-friendly CSV** export (`utf-8-sig` / BOM for Cyrillic)
- a **ChatGPT-ready JSONL** export (clean text, UTF‑8, one record per line)

It is designed to be **incremental**:
- each run pulls **new messages** since the last run quickly (checkpoint-based)
- it can also rescan a recent window to catch **message edits** and mark **deletions** (best-effort)
- exports are rebuilt only when changes are detected

---

## What it creates

- **SQLite database**: `telegram_messages.db` (configurable)
- **CSV export**: `telegram_messages.csv` (configurable)
- **ChatGPT export (JSONL)**: `chatgpt_export.jsonl` (configurable)

> These outputs are intended to stay local (they should not be committed to Git). See `.gitignore`.

---

## Requirements

- Python 3.10+ recommended (works on Windows)
- Telegram API credentials (`API_ID`, `API_HASH`)

Install dependencies:

```powershell
pip install -r requirements.txt
```

---

## Configure `.env`

Create a `.env` file in the project root (same folder as `scrape_telegram.py`).

Example:

```env
API_ID=28697746
API_HASH=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
SESSION_NAME=telethon_session

# You can use a username, @username, or a t.me link:
# CHAT_IDENTIFIER=cyprusithr
# CHAT_IDENTIFIER=@cyprusithr
CHAT_IDENTIFIER=https://t.me/cyprusithr/46679

# Optional: if you want to force the topic/thread id:
# TOPIC_ID=46679

OUTPUT_DB=telegram_messages.db
OUTPUT_CSV=telegram_messages.csv
OUTPUT_CHATGPT=chatgpt_export.jsonl

# Scrape window (e.g. 365 days ~= 12 months)
DAYS_BACK=365

# How far back to rescan for edits/deletes (defaults to DAYS_BACK)
EDIT_LOOKBACK_DAYS=365

# Optional ChatGPT export thinning (defaults off)
MIN_CHARS=0
SKIP_HASHTAG_ONLY=0
INCLUDE_DELETED=0
INCLUDE_SERVICE=0
```

---

## Maintenance & Best Practices

See the following guides:
- `MAINTENANCE.md` - Maintenance procedures and schedules
- `BEST_PRACTICES.md` - Coding standards and best practices
- `TROUBLESHOOTING.md` - Common issues and solutions

### Quick Maintenance

```powershell
# Run all maintenance tasks
python maintenance.py --all

# Check database health
python maintenance.py --check-dbs

# Cleanup old files
python cleanup.py
```

## Run the scraper

First run will prompt for:
- your phone number
- login code from Telegram
- (if enabled) your Telegram 2-step verification password

Run:

```powershell
python scrape_telegram.py
```

What happens:
- **DB is updated** (new messages inserted; edited messages updated; deletions marked in-window)
- if changes were detected, it rebuilds:
  - `telegram_messages.csv`
  - `chatgpt_export.jsonl` (if `OUTPUT_CHATGPT` is set)

---

## Export for ChatGPT (JSONL)

You can export JSONL independently:

```powershell
python export_chatgpt.py
```

JSONL format: one JSON object per line, UTF‑8, Cyrillic preserved (`ensure_ascii=False`), with fields like:
- `chat`, `topic_id`, `message_id`
- `date`, `edit_date`
- `sender_id`, `sender_username`
- `deleted`, `is_service`
- `text` (cleaned)

---

## Notes / Safety

- **Do not commit `.env` or session files**. They contain secrets and login session data.
- Telegram has rate limits; the scraper includes basic `FloodWait` handling.
- Deletion detection is **best-effort** within the configured window.

---

## Web UI (local)

This repo includes a minimal local web interface in `web_app.py`:

- paste a Telegram link (channel/group/topic)
- validate that it exists
- see **earliest message month/year** (fast probe)
- start a scrape job (currently **full history** into a separate DB per job)

## Running the Web Server

**Important:** Use the startup script to avoid port conflicts:

```powershell
python start_server.py
```

Or manually:

```powershell
python -m uvicorn web_app:app --reload --port 8000
```

### Troubleshooting: Port Already in Use

If you see "port already in use" errors or the server seems to serve old code:

1. **Use the startup script** (`start_server.py`) - it automatically kills old processes
2. **Or manually kill processes:**
   ```powershell
   # Find processes on port 8000
   netstat -ano | findstr :8000
   
   # Kill them (replace PID with actual process ID)
   taskkill /F /PID <PID>
   ```
3. **Or use a different port:**
   ```powershell
   python -m uvicorn web_app:app --reload --port 8001
   ```

**Note:** The web interface has been removed. Only API endpoints are available.

### API Endpoints

- `POST /validate` - Validate a Telegram chat/channel
- `POST /scrape` - Start a scraping job
- `GET /status/{job_id}` - Get job status
- `GET /download/{job_id}/{kind}` - Download results (csv/jsonl)
- `GET /health` - System health check
- `GET /health/database/{db_name}` - Database health check
- `GET /api/databases` - List all databases
- `GET /api/stats/{db_name}` - Get database statistics
- `GET /api/chat-info/{chat_identifier}` - Get chat info from Telegram
- `POST /api/update/{db_name}` - Update a database
- `DELETE /api/delete/{db_name}` - Archive a database
- `POST /api/cleanup-archives` - Clean up old archives
