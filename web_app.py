import asyncio
import os
import uuid
import sqlite3
import glob
import shutil
from datetime import datetime, timezone, timedelta
from typing import Optional
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from pydantic import BaseModel, Field
from telethon import TelegramClient
from telethon.errors import UsernameInvalidError, UsernameNotOccupiedError, ChannelPrivateError

from scrape_telegram import load_config, parse_chat_identifier, _topic_id_norm
from export_messages import export_to_csv
from export_chatgpt import export_chatgpt_jsonl, load_config as load_export_cfg
from backfill_to_separate_db import backfill as backfill_full
from logger_config import setup_logging, get_logger
from health import check_database_health, check_system_health

# Set up logging
setup_logging(level=os.getenv("LOG_LEVEL", "INFO"))
logger = get_logger(__name__)

app = FastAPI(
    title="TG Work Checker",
    description="Telegram message scraper and analyzer",
    version="0.3.0"
)
APP_VERSION = "0.3.0"


class ValidateRequest(BaseModel):
    chat: str = Field(..., description="t.me link, @username, or username")
    topic_id: Optional[int] = Field(None, description="Optional forum topic/thread id")


class ValidateResponse(BaseModel):
    ok: bool
    chat_identifier: str
    topic_id: int
    title: Optional[str]
    description: Optional[str]
    latest_message_id: Optional[int]
    earliest_message_month_year: Optional[str]


class ScrapeRequest(BaseModel):
    chat: str
    topic_id: Optional[int] = None
    mode: str = Field(..., description="full | range")
    date_from: Optional[str] = Field(None, description="YYYY-MM-DD (mode=range)")
    date_to: Optional[str] = Field(None, description="YYYY-MM-DD (mode=range)")
    output_db: Optional[str] = None


class JobStatus(BaseModel):
    job_id: str
    status: str  # queued|running|done|error
    message: Optional[str] = None
    scanned: int = 0
    new: int = 0
    updated: int = 0
    output_db: Optional[str] = None
    output_csv: Optional[str] = None
    output_jsonl: Optional[str] = None


JOBS: dict[str, JobStatus] = {}

_CLIENT: TelegramClient | None = None
_CLIENT_LOCK = asyncio.Lock()


async def get_client():
    """
    Reuse a single Telethon client to avoid session DB locks.
    Also use a dedicated session name for the web app so it won't conflict with CLI scripts.
    """
    global _CLIENT
    async with _CLIENT_LOCK:
        if _CLIENT is not None:
            return _CLIENT

        try:
            cfg = load_config({})
            web_session = os.getenv("WEB_SESSION_NAME") or f"{cfg['session_name']}_web"
            logger.info(f"Initializing Telethon client with session: {web_session}")
            client = TelegramClient(web_session, cfg["api_id"], cfg["api_hash"], sequential_updates=True)
            
            # Important: do NOT call start() here because it may prompt for phone/code inside the web server.
            await client.connect()
            if not await client.is_user_authorized():
                logger.warning("Telethon session not authorized")
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Telethon session is not authorized for the web app. "
                        "Create it once by running a CLI login with WEB_SESSION_NAME, e.g.:\n"
                        "  setx WEB_SESSION_NAME telethon_session_web\n"
                        "  .\\.venv\\Scripts\\python.exe scrape_telegram.py\n"
                        "Then restart the web server."
                    ),
                )
            logger.info("Telethon client initialized successfully")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to initialize Telethon client: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Telethon session error: {e}")
        _CLIENT = client
        return _CLIENT


@app.on_event("startup")
async def startup_event():
    """Clean up old archives on server startup."""
    try:
        result = cleanup_old_archives(90)
        if result["deleted"] > 0:
            print(f"Startup cleanup: Deleted {result['deleted']} archived file(s) older than 90 days.")
        if result["errors"]:
            print(f"Startup cleanup errors: {result['errors']}")
    except Exception as e:
        print(f"Startup cleanup error: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    global _CLIENT
    if _CLIENT is not None:
        try:
            await _CLIENT.disconnect()
        finally:
            _CLIENT = None


async def earliest_month_year_fast(client: TelegramClient, entity, latest_id: int) -> Optional[str]:
    """
    Fast-ish earliest-message probe via binary search on message ids.
    Returns "YYYY-MM" or None if cannot determine.
    """
    lo, hi = 1, latest_id
    earliest_msg = None

    async def exists(mid: int):
        m = await client.get_messages(entity, ids=mid)
        return m if m and getattr(m, "id", None) == mid else None

    # Find any existing message near the low end (some chats may not have id=1 visible)
    # We'll still binary search for the first existing id.
    while lo <= hi:
        mid = (lo + hi) // 2
        m = await exists(mid)
        if m is None:
            lo = mid + 1
        else:
            earliest_msg = m
            hi = mid - 1

    if earliest_msg and earliest_msg.date:
        # Telethon returns aware datetime (UTC)
        d = earliest_msg.date.astimezone(timezone.utc)
        return d.strftime("%Y-%m")
    return None




@app.post("/validate", response_model=ValidateResponse)
async def validate(req: ValidateRequest):
    """Validate a Telegram chat/channel and return its information."""
    try:
        logger.info(f"Validating chat: {req.chat}")
        chat_identifier, topic_id_from_url = parse_chat_identifier(req.chat, req.topic_id)
        topic_id = _topic_id_norm(req.topic_id if req.topic_id is not None else topic_id_from_url)

        client = await get_client()
        entity = await client.get_entity(chat_identifier)
        title = getattr(entity, "title", None)
        # Get description/about text (available for channels and some groups)
        description = getattr(entity, "about", None) or getattr(entity, "description", None)

        latest = await client.get_messages(entity, limit=1)
        latest_id = latest[0].id if latest and len(latest) else None
        earliest = None
        if latest_id is not None:
            earliest = await earliest_month_year_fast(client, entity, latest_id)
        
        logger.info(f"Validation successful: {chat_identifier}, topic_id={topic_id}, messages up to ID {latest_id}")
        
        return ValidateResponse(
            ok=True,
            chat_identifier=chat_identifier,
            topic_id=topic_id,
            title=title,
            description=description,
            latest_message_id=latest_id,
            earliest_message_month_year=earliest,
        )
    except (UsernameInvalidError, UsernameNotOccupiedError, ChannelPrivateError) as e:
        logger.warning(f"Validation failed for {req.chat}: {e}")
        raise HTTPException(status_code=400, detail=f"Unable to access chat: {e}")
    except Exception as e:
        logger.error(f"Unexpected error during validation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Validation error: {e}")


async def run_job(job_id: str, req: ScrapeRequest):
    JOBS[job_id].status = "running"
    try:
        # For now: full history backfill only (range mode can be added next)
        if req.mode not in ("full", "range"):
            raise ValueError("mode must be 'full' or 'range'")
        if req.mode == "range":
            raise ValueError("range mode not implemented yet in web UI backend")

        def safe_name(s: str) -> str:
            s = (s or "").strip()
            s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
            s = re.sub(r"_+", "_", s).strip("._-")
            return s or "export"

        output_dir = os.getenv("OUTPUT_DIR") or "exports"
        os.makedirs(output_dir, exist_ok=True)

        # Default filenames if user didn't specify:
        # <chat>_Export.csv / <chat>_Export.jsonl (and a matching .db)
        chat_identifier_default, topic_from_url = parse_chat_identifier(req.chat, req.topic_id)
        topic_norm = _topic_id_norm(req.topic_id if req.topic_id is not None else topic_from_url)
        base = safe_name(chat_identifier_default)
        if topic_norm != -1:
            base = f"{base}_{topic_norm}"

        if req.output_db:
            # Prevent accidental URL pasted into output filename field (Windows will error on "https:")
            if "://" in req.output_db or req.output_db.lower().startswith("http"):
                raise ValueError(
                    "Output DB filename looks like a URL. Leave it blank or enter something like: my_export.db"
                )
            out_db = req.output_db
            out_dir = os.path.dirname(out_db)
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
        else:
            out_db = os.path.join(output_dir, f"{base}.db")
        cfg = load_config(
            {
                "chat": req.chat,
                "topic_id": req.topic_id,
                "output_db": out_db,
                "output_csv": None,
            }
        )

        # Inline backfill with progress updates for the UI
        api_id = cfg["api_id"]
        api_hash = cfg["api_hash"]
        session_name = cfg["session_name"]
        chat_identifier = cfg["chat_identifier"]
        topic_id = _topic_id_norm(cfg.get("topic_id"))

        from scrape_telegram import init_db

        conn = init_db(out_db)
        cur = conn.cursor()

        client = TelegramClient(session_name, api_id, api_hash)
        await client.start()
        entity = await client.get_entity(chat_identifier)
        chat_id = int(getattr(entity, "id", 0)) if getattr(entity, "id", None) is not None else None

        iter_kwargs = {}
        if topic_id != -1:
            iter_kwargs["reply_to"] = topic_id

        run_ts = datetime.now(timezone.utc).isoformat()

        UPSERT_SQL = """
        INSERT INTO messages (
            chat_id, chat_identifier, topic_id, message_id, date, edit_date,
            sender_id, sender_username, text, reply_to_msg_id, is_service, deleted, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
        ON CONFLICT(chat_identifier, topic_id, message_id) DO UPDATE SET
            chat_id=excluded.chat_id,
            date=excluded.date,
            edit_date=excluded.edit_date,
            sender_id=excluded.sender_id,
            sender_username=excluded.sender_username,
            text=excluded.text,
            reply_to_msg_id=excluded.reply_to_msg_id,
            is_service=excluded.is_service,
            deleted=0,
            updated_at=excluded.updated_at
        WHERE
            COALESCE(messages.edit_date, '') != COALESCE(excluded.edit_date, '')
            OR COALESCE(messages.text, '') != COALESCE(excluded.text, '')
            OR COALESCE(messages.sender_id, -1) != COALESCE(excluded.sender_id, -1)
            OR COALESCE(messages.sender_username, '') != COALESCE(excluded.sender_username, '')
            OR COALESCE(messages.reply_to_msg_id, -1) != COALESCE(excluded.reply_to_msg_id, -1)
            OR COALESCE(messages.is_service, 0) != COALESCE(excluded.is_service, 0)
            OR COALESCE(messages.deleted, 0) != 0
        """

        batch = []

        def flush():
            if not batch:
                return 0
            before = conn.total_changes
            cur.executemany(UPSERT_SQL, batch)
            delta = conn.total_changes - before
            batch.clear()
            conn.commit()
            return delta

        scanned = 0
        new_count = 0
        upd_count = 0

        async for msg in client.iter_messages(entity, reverse=False, **iter_kwargs):
            if msg is None or msg.date is None:
                continue

            cur.execute(
                "SELECT 1 FROM messages WHERE chat_identifier=? AND topic_id=? AND message_id=? LIMIT 1",
                (chat_identifier, topic_id, msg.id),
            )
            if cur.fetchone() is None:
                new_count += 1

            text = msg.message or ""
            is_service = 1 if msg.action is not None else 0
            edit_date = msg.edit_date.isoformat() if getattr(msg, "edit_date", None) else None

            batch.append(
                (
                    chat_id,
                    chat_identifier,
                    topic_id,
                    msg.id,
                    msg.date.isoformat(),
                    edit_date,
                    msg.sender_id if hasattr(msg, "sender_id") else None,
                    getattr(getattr(msg, "sender", None), "username", None),
                    text,
                    msg.reply_to_msg_id,
                    is_service,
                    run_ts,
                )
            )
            scanned += 1

            if scanned % 500 == 0:
                delta = flush()
                # delta includes inserts+updates; approximate updates as delta-new
                upd_count = max(0, delta - new_count)
                JOBS[job_id].scanned = scanned
                JOBS[job_id].new = new_count
                JOBS[job_id].updated = upd_count
                JOBS[job_id].message = f"Scraping... scanned {scanned}"

        delta = flush()
        upd_count = max(upd_count, max(0, delta - new_count))

        await client.disconnect()
        conn.close()

        JOBS[job_id].scanned = scanned
        JOBS[job_id].new = new_count
        JOBS[job_id].updated = upd_count
        JOBS[job_id].message = f"Completed: scraped {scanned} messages"

        # Exports (defaulting to <chat>_Export.*)
        out_csv = os.path.join(output_dir, f"{base}_Export.csv")
        out_jsonl = os.path.join(output_dir, f"{base}_Export.jsonl")

        export_to_csv(out_db, out_csv)
        os.environ["OUTPUT_DB"] = out_db
        os.environ["OUTPUT_CHATGPT"] = out_jsonl
        export_chatgpt_jsonl(load_export_cfg())

        JOBS[job_id].status = "done"
        # Add download targets
        JOBS[job_id].output_db = out_db
        JOBS[job_id].output_csv = out_csv
        JOBS[job_id].output_jsonl = out_jsonl
    except Exception as e:
        JOBS[job_id].status = "error"
        JOBS[job_id].message = str(e)


@app.post("/scrape", response_model=JobStatus)
async def scrape(req: ScrapeRequest):
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = JobStatus(job_id=job_id, status="queued")
    asyncio.create_task(run_job(job_id, req))
    return JOBS[job_id]


@app.get("/status/{job_id}", response_model=JobStatus)
def status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/download/{job_id}/{kind}")
def download(job_id: str, kind: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    path = None
    if kind == "db":
        path = job.output_db
    elif kind == "csv":
        path = job.output_csv
    elif kind == "jsonl":
        path = job.output_jsonl
    else:
        raise HTTPException(status_code=400, detail="kind must be db|csv|jsonl")
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not available")
    # Suggest a friendly filename to the browser
    filename = os.path.basename(path)
    return FileResponse(path, filename=filename)


def get_db_stats(db_path: str):
    """Get statistics from a database file."""
    if not os.path.exists(db_path):
        logger.warning(f"Database not found: {db_path}")
        return None
    
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        
        # Check if messages table exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
        if not cur.fetchone():
            conn.close()
            logger.warning(f"No messages table in {db_path}")
            return {"error": "No messages table found in database"}
        
        # Count messages
        cur.execute("SELECT COUNT(*) FROM messages")
        count = cur.fetchone()[0]
        
        # Earliest and latest dates
        cur.execute("SELECT MIN(date), MAX(date) FROM messages WHERE date IS NOT NULL")
        row = cur.fetchone()
        earliest = row[0] if row and row[0] else None
        latest = row[1] if row and row[1] else None
        
        # Get chat identifier(s) and topic info
        cur.execute("SELECT DISTINCT chat_identifier, topic_id FROM messages LIMIT 10")
        chats = cur.fetchall()
        
        conn.close()
        
        logger.debug(f"Stats for {db_path}: {count} messages")
        
        return {
            "count": count,
            "earliest": earliest,
            "latest": latest,
            "chats": [{"identifier": c[0], "topic_id": c[1]} for c in chats],
        }
    except Exception as e:
        logger.error(f"Error getting stats for {db_path}: {e}", exc_info=True)
        return {"error": str(e)}


def get_archive_dir():
    """Get or create the archive directory."""
    archive_dir = os.getenv("ARCHIVE_DIR", "archived")
    os.makedirs(archive_dir, exist_ok=True)
    return archive_dir


def archive_file(file_path: str) -> str:
    """
    Move a file to the archive directory with a timestamp.
    Returns the archived file path.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    
    archive_dir = get_archive_dir()
    filename = os.path.basename(file_path)
    # Add timestamp: filename_YYYYMMDD_HHMMSS.ext
    name, ext = os.path.splitext(filename)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    archived_name = f"{name}_{timestamp}{ext}"
    archived_path = os.path.join(archive_dir, archived_name)
    
    shutil.move(file_path, archived_path)
    return archived_path


def cleanup_old_archives(days: int = 90):
    """
    Delete archived files older than the specified number of days.
    Default is 90 days (3 months).
    """
    archive_dir = get_archive_dir()
    if not os.path.exists(archive_dir):
        return {"deleted": 0, "errors": []}
    
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
    deleted_count = 0
    errors = []
    
    for file_path in glob.glob(os.path.join(archive_dir, "*")):
        if not os.path.isfile(file_path):
            continue
        
        try:
            # Extract timestamp from filename: name_YYYYMMDD_HHMMSS.ext
            filename = os.path.basename(file_path)
            # Try to parse timestamp from filename
            match = re.search(r"_(\d{8}_\d{6})", filename)
            if match:
                timestamp_str = match.group(1)
                file_date = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                file_date = file_date.replace(tzinfo=timezone.utc)
                
                if file_date < cutoff_date:
                    os.remove(file_path)
                    deleted_count += 1
            else:
                # If we can't parse timestamp, use file modification time
                mtime = datetime.fromtimestamp(os.path.getmtime(file_path), tz=timezone.utc)
                if mtime < cutoff_date:
                    os.remove(file_path)
                    deleted_count += 1
        except Exception as e:
            errors.append(f"Error deleting {file_path}: {e}")
    
    return {"deleted": deleted_count, "errors": errors}


@app.get("/api/databases")
def list_databases():
    """List all available database files."""
    dbs = []
    # Check root directory
    for db in glob.glob("*.db"):
        if os.path.isfile(db) and not db.startswith("."):
            dbs.append({"name": db, "path": db})
    # Check exports directory
    if os.path.exists("exports"):
        for db in glob.glob("exports/*.db"):
            if os.path.isfile(db):
                dbs.append({"name": os.path.basename(db), "path": db})
    # Check merged directory
    if os.path.exists("merged"):
        for db in glob.glob("merged/*.db"):
            if os.path.isfile(db):
                dbs.append({"name": os.path.basename(db), "path": db})
    return {"databases": dbs}


@app.get("/api/stats/{db_name:path}")
def get_stats(db_name: str):
    """Get statistics for a specific database."""
    # Sanitize path to prevent directory traversal
    if ".." in db_name or db_name.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid database path")
    
    stats = get_db_stats(db_name)
    if stats is None:
        raise HTTPException(status_code=404, detail="Database not found")
    if "error" in stats:
        raise HTTPException(status_code=500, detail=stats["error"])
    
    return stats


@app.get("/api/chat-info/{chat_identifier:path}")
async def get_chat_info(chat_identifier: str):
    """Get chat name and description from Telegram for a given chat identifier."""
    try:
        client = await get_client()
        entity = await client.get_entity(chat_identifier)
        title = getattr(entity, "title", None)
        description = getattr(entity, "about", None) or getattr(entity, "description", None)
        return {
            "chat_identifier": chat_identifier,
            "title": title,
            "description": description,
        }
    except Exception as e:
        # If we can't fetch, return None values (don't fail the dashboard)
        return {
            "chat_identifier": chat_identifier,
            "title": None,
            "description": None,
            "error": str(e),
        }


@app.post("/api/update/{db_name:path}")
async def trigger_update(db_name: str):
    """Trigger an update (re-run scraper) for a database."""
    # Sanitize path
    if ".." in db_name or db_name.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid database path")
    
    if not os.path.exists(db_name):
        raise HTTPException(status_code=404, detail="Database not found")
    
    # Try to infer chat identifier from the database
    try:
        conn = sqlite3.connect(db_name)
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT chat_identifier, topic_id FROM messages LIMIT 1")
        row = cur.fetchone()
        conn.close()
        
        if not row:
            raise HTTPException(status_code=400, detail="Cannot determine chat from database")
        
        chat_identifier, topic_id = row[0], row[1]
        
        # Create a scrape job
        req = ScrapeRequest(
            chat=chat_identifier,
            topic_id=topic_id if topic_id != -1 else None,
            mode="full",
            output_db=db_name,
        )
        
        job_id = uuid.uuid4().hex[:12]
        JOBS[job_id] = JobStatus(job_id=job_id, status="queued")
        asyncio.create_task(run_job(job_id, req))
        return {"job_id": job_id, "message": "Update job started"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error starting update: {e}")


@app.delete("/api/delete/{db_name:path}")
def delete_database(db_name: str):
    """
    Delete a database and its related files (CSV, JSONL).
    Files are archived for 3 months before permanent deletion.
    """
    # Sanitize path
    if ".." in db_name or db_name.startswith("/"):
        logger.warning(f"Invalid database path attempted: {db_name}")
        raise HTTPException(status_code=400, detail="Invalid database path")
    
    if not os.path.exists(db_name):
        logger.warning(f"Database not found for deletion: {db_name}")
        raise HTTPException(status_code=404, detail="Database not found")
    
    logger.info(f"Archiving database: {db_name}")
    archived_files = []
    errors = []
    
    try:
        # Archive the database file
        archived_db = archive_file(db_name)
        archived_files.append(archived_db)
        logger.info(f"Archived database: {archived_db}")
        
        # Find and archive related CSV and JSONL files
        base_name = os.path.splitext(os.path.basename(db_name))[0]
        base_path = os.path.dirname(db_name) if os.path.dirname(db_name) else "."
        
        # Look for CSV file
        csv_patterns = [
            os.path.join(base_path, f"{base_name}.csv"),
            os.path.join(base_path, f"{base_name}_Export.csv"),
            os.path.join("exports", f"{base_name}.csv"),
            os.path.join("exports", f"{base_name}_Export.csv"),
        ]
        for csv_path in csv_patterns:
            if os.path.exists(csv_path):
                try:
                    archived_csv = archive_file(csv_path)
                    archived_files.append(archived_csv)
                    logger.info(f"Archived CSV: {archived_csv}")
                except Exception as e:
                    logger.error(f"Error archiving CSV {csv_path}: {e}", exc_info=True)
                    errors.append(f"Error archiving CSV {csv_path}: {e}")
        
        # Look for JSONL file
        jsonl_patterns = [
            os.path.join(base_path, f"{base_name}.jsonl"),
            os.path.join(base_path, f"{base_name}_Export.jsonl"),
            os.path.join("exports", f"{base_name}.jsonl"),
            os.path.join("exports", f"{base_name}_Export.jsonl"),
        ]
        for jsonl_path in jsonl_patterns:
            if os.path.exists(jsonl_path):
                try:
                    archived_jsonl = archive_file(jsonl_path)
                    archived_files.append(archived_jsonl)
                    logger.info(f"Archived JSONL: {archived_jsonl}")
                except Exception as e:
                    logger.error(f"Error archiving JSONL {jsonl_path}: {e}", exc_info=True)
                    errors.append(f"Error archiving JSONL {jsonl_path}: {e}")
        
        logger.info(f"Successfully archived {len(archived_files)} file(s) for {db_name}")
        
        return {
            "success": True,
            "message": f"Database and related files archived. {len(archived_files)} file(s) archived.",
            "archived_files": archived_files,
            "errors": errors if errors else None,
        }
    except Exception as e:
        logger.error(f"Error archiving database {db_name}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error archiving database: {e}")


@app.post("/api/cleanup-archives")
def cleanup_archives(days: int = Query(90, description="Number of days to keep archives")):
    """
    Clean up archived files older than the specified number of days.
    Default is 90 days (3 months).
    """
    try:
        result = cleanup_old_archives(days)
        return {
            "success": True,
            "deleted_count": result["deleted"],
            "errors": result["errors"] if result["errors"] else None,
            "message": f"Deleted {result['deleted']} archived file(s) older than {days} days.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error cleaning up archives: {e}")


@app.get("/", response_class=HTMLResponse)
def read_root():
    """Serve the main web UI."""
    return get_main_ui_html()


def get_main_ui_html():
    """Generate the main UI HTML with dark theme."""
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TG Work Checker</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        :root {
            --bg-primary: #0a0a0a;
            --bg-secondary: #1a1a1a;
            --bg-tertiary: #252525;
            --text-primary: #ffffff;
            --text-secondary: #a0a0a0;
            --accent: #007aff;
            --accent-hover: #0051d5;
            --success: #34c759;
            --warning: #ff9500;
            --error: #ff3b30;
            --border: #2a2a2a;
            --shadow: rgba(0, 0, 0, 0.3);
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.6;
            min-height: 100vh;
            padding: 2rem;
        }
        
        .container {
            max-width: 800px;
            margin: 0 auto;
        }
        
        .header {
            text-align: center;
            margin-bottom: 3rem;
            padding-bottom: 2rem;
            border-bottom: 1px solid var(--border);
        }
        
        .header h1 {
            font-size: 2.5rem;
            font-weight: 600;
            margin-bottom: 0.5rem;
            letter-spacing: -0.02em;
        }
        
        .header p {
            color: var(--text-secondary);
            font-size: 1rem;
        }
        
        .card {
            background: var(--bg-secondary);
            border-radius: 16px;
            padding: 2rem;
            margin-bottom: 1.5rem;
            border: 1px solid var(--border);
            box-shadow: 0 4px 20px var(--shadow);
            transition: transform 0.2s, box-shadow 0.2s;
        }
        
        .card:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 30px var(--shadow);
        }
        
        .card-title {
            font-size: 1.25rem;
            font-weight: 600;
            margin-bottom: 1rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        
        .icon {
            width: 20px;
            height: 20px;
            stroke: currentColor;
            fill: none;
            stroke-width: 2;
            stroke-linecap: round;
            stroke-linejoin: round;
        }
        
        .input-group {
            margin-bottom: 1.5rem;
        }
        
        .input-group label {
            display: block;
            margin-bottom: 0.5rem;
            color: var(--text-secondary);
            font-size: 0.875rem;
            font-weight: 500;
        }
        
        .input {
            width: 100%;
            padding: 0.875rem 1rem;
            background: var(--bg-tertiary);
            border: 1px solid var(--border);
            border-radius: 10px;
            color: var(--text-primary);
            font-size: 1rem;
            transition: border-color 0.2s, background 0.2s;
        }
        
        .input:focus {
            outline: none;
            border-color: var(--accent);
            background: var(--bg-secondary);
        }
        
        .input::placeholder {
            color: var(--text-secondary);
        }
        
        .btn {
            padding: 0.875rem 1.5rem;
            border: none;
            border-radius: 10px;
            font-size: 1rem;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s;
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            text-decoration: none;
        }
        
        .btn-primary {
            background: var(--accent);
            color: white;
        }
        
        .btn-primary:hover:not(:disabled) {
            background: var(--accent-hover);
            transform: translateY(-1px);
        }
        
        .btn-secondary {
            background: var(--bg-tertiary);
            color: var(--text-primary);
            border: 1px solid var(--border);
        }
        
        .btn-secondary:hover:not(:disabled) {
            background: var(--bg-secondary);
        }
        
        .btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        
        .btn-group {
            display: flex;
            gap: 1rem;
            flex-wrap: wrap;
        }
        
        .status {
            padding: 1rem;
            border-radius: 10px;
            margin-bottom: 1rem;
            display: none;
        }
        
        .status.show {
            display: block;
        }
        
        .status-info {
            background: rgba(0, 122, 255, 0.1);
            border: 1px solid rgba(0, 122, 255, 0.3);
            color: var(--accent);
        }
        
        .status-success {
            background: rgba(52, 199, 89, 0.1);
            border: 1px solid rgba(52, 199, 89, 0.3);
            color: var(--success);
        }
        
        .status-error {
            background: rgba(255, 59, 48, 0.1);
            border: 1px solid rgba(255, 59, 48, 0.3);
            color: var(--error);
        }
        
        .loading {
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
        }
        
        .spinner {
            width: 16px;
            height: 16px;
            border: 2px solid rgba(255, 255, 255, 0.3);
            border-top-color: currentColor;
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
        }
        
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        
        .progress {
            margin-top: 1rem;
        }
        
        .progress-bar {
            width: 100%;
            height: 4px;
            background: var(--bg-tertiary);
            border-radius: 2px;
            overflow: hidden;
        }
        
        .progress-fill {
            height: 100%;
            background: var(--accent);
            transition: width 0.3s ease;
            width: 0%;
        }
        
        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
            gap: 1rem;
            margin-top: 1rem;
        }
        
        .stat {
            text-align: center;
        }
        
        .stat-value {
            font-size: 1.5rem;
            font-weight: 600;
            color: var(--accent);
        }
        
        .stat-label {
            font-size: 0.875rem;
            color: var(--text-secondary);
            margin-top: 0.25rem;
        }
        
        .downloads {
            display: flex;
            gap: 1rem;
            flex-wrap: wrap;
            margin-top: 1rem;
        }
        
        .hidden {
            display: none;
        }
        
        .fade-in {
            animation: fadeIn 0.3s ease;
        }
        
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>TG Work Checker</h1>
            <p>Telegram Message Scraper & Analyzer</p>
        </div>
        
        <!-- Validation Card -->
        <div class="card">
            <div class="card-title">
                <svg class="icon" viewBox="0 0 24 24">
                    <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path>
                    <circle cx="12" cy="10" r="3"></circle>
                </svg>
                Validate Chat
            </div>
            <div class="input-group">
                <label for="chat-input">Chat Link or Username</label>
                <input type="text" id="chat-input" class="input" placeholder="https://t.me/cyprusithr/46679 or @username">
            </div>
            <div class="input-group">
                <label for="topic-input">Topic ID (Optional)</label>
                <input type="number" id="topic-input" class="input" placeholder="46679">
            </div>
            <button class="btn btn-primary" onclick="validateChat()">
                <svg class="icon" viewBox="0 0 24 24">
                    <polyline points="20 6 9 17 4 12"></polyline>
                </svg>
                Validate
            </button>
            <div id="validate-status" class="status"></div>
        </div>
        
        <!-- Scraping Card -->
        <div class="card">
            <div class="card-title">
                <svg class="icon" viewBox="0 0 24 24">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                    <polyline points="7 10 12 15 17 10"></polyline>
                    <line x1="12" y1="15" x2="12" y2="3"></line>
                </svg>
                Start Scraping
            </div>
            <div class="input-group">
                <label for="scrape-chat">Chat Link or Username</label>
                <input type="text" id="scrape-chat" class="input" placeholder="https://t.me/cyprusithr/46679">
            </div>
            <div class="input-group">
                <label for="scrape-topic">Topic ID (Optional)</label>
                <input type="number" id="scrape-topic" class="input" placeholder="46679">
            </div>
            <div class="input-group">
                <label for="output-db">Output DB Name (Optional)</label>
                <input type="text" id="output-db" class="input" placeholder="Leave blank for auto-naming">
            </div>
            <button class="btn btn-primary" onclick="startScrape()">
                <svg class="icon" viewBox="0 0 24 24">
                    <polygon points="5 3 19 12 5 21 5 3"></polygon>
                </svg>
                Start Scraping
            </button>
            <div id="scrape-status" class="status"></div>
            <div id="scrape-progress" class="progress hidden">
                <div class="progress-bar">
                    <div class="progress-fill" id="progress-fill"></div>
                </div>
                <div class="stats" id="scrape-stats"></div>
            </div>
        </div>
        
        <!-- Job Status Card -->
        <div class="card hidden" id="job-card">
            <div class="card-title">
                <svg class="icon" viewBox="0 0 24 24">
                    <circle cx="12" cy="12" r="10"></circle>
                    <polyline points="12 6 12 12 16 14"></polyline>
                </svg>
                Job Status
            </div>
            <div id="job-status-content"></div>
            <div class="downloads" id="download-links"></div>
        </div>
        
        <!-- Databases Card -->
        <div class="card">
            <div class="card-title">
                <svg class="icon" viewBox="0 0 24 24">
                    <ellipse cx="12" cy="5" rx="9" ry="3"></ellipse>
                    <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"></path>
                    <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"></path>
                </svg>
                Databases
            </div>
            <button class="btn btn-secondary" onclick="loadDatabases()">
                <svg class="icon" viewBox="0 0 24 24">
                    <polyline points="23 4 23 10 17 10"></polyline>
                    <polyline points="1 20 1 14 7 14"></polyline>
                    <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
                </svg>
                Refresh
            </button>
            <div id="databases-list" style="margin-top: 1rem;"></div>
        </div>
    </div>
    
    <script>
        let currentJobId = null;
        let statusInterval = null;
        
        async function validateChat() {
            const chat = document.getElementById('chat-input').value.trim();
            const topic = document.getElementById('topic-input').value.trim();
            const statusDiv = document.getElementById('validate-status');
            
            if (!chat) {
                showStatus('validate-status', 'Please enter a chat link or username', 'error');
                return;
            }
            
            showStatus('validate-status', 'Validating...', 'info');
            
            try {
                const response = await fetch('/validate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        chat: chat,
                        topic_id: topic ? parseInt(topic) : null
                    })
                });
                
                const data = await response.json();
                
                if (data.ok) {
                    let msg = `✓ Valid chat: ${data.chat_identifier}`;
                    if (data.title) msg += ` - ${data.title}`;
                    if (data.earliest_message_month_year) {
                        msg += `\\nMessages dating back to ${data.earliest_message_month_year}`;
                    }
                    showStatus('validate-status', msg, 'success');
                } else {
                    showStatus('validate-status', 'Invalid chat or cannot access', 'error');
                }
            } catch (error) {
                showStatus('validate-status', 'Error: ' + error.message, 'error');
            }
        }
        
        async function startScrape() {
            const chat = document.getElementById('scrape-chat').value.trim();
            const topic = document.getElementById('scrape-topic').value.trim();
            const outputDb = document.getElementById('output-db').value.trim();
            const statusDiv = document.getElementById('scrape-status');
            
            if (!chat) {
                showStatus('scrape-status', 'Please enter a chat link or username', 'error');
                return;
            }
            
            showStatus('scrape-status', 'Starting scrape...', 'info');
            document.getElementById('scrape-progress').classList.remove('hidden');
            
            try {
                const response = await fetch('/scrape', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        chat: chat,
                        topic_id: topic ? parseInt(topic) : null,
                        mode: 'full',
                        output_db: outputDb || null
                    })
                });
                
                const data = await response.json();
                currentJobId = data.job_id;
                
                showStatus('scrape-status', 'Scraping started. Job ID: ' + currentJobId, 'info');
                document.getElementById('job-card').classList.remove('hidden');
                
                // Start polling for status
                if (statusInterval) clearInterval(statusInterval);
                statusInterval = setInterval(checkJobStatus, 1000);
                checkJobStatus();
            } catch (error) {
                showStatus('scrape-status', 'Error: ' + error.message, 'error');
            }
        }
        
        async function checkJobStatus() {
            if (!currentJobId) return;
            
            try {
                const response = await fetch(`/status/${currentJobId}`);
                const data = await response.json();
                
                updateJobStatus(data);
                
                if (data.status === 'done' || data.status === 'error') {
                    if (statusInterval) {
                        clearInterval(statusInterval);
                        statusInterval = null;
                    }
                }
            } catch (error) {
                console.error('Status check error:', error);
            }
        }
        
        function updateJobStatus(data) {
            const contentDiv = document.getElementById('job-status-content');
            const statsDiv = document.getElementById('scrape-stats');
            const downloadsDiv = document.getElementById('download-links');
            
            let statusHtml = '';
            let statsHtml = '';
            let downloadsHtml = '';
            
            if (data.status === 'running') {
                statusHtml = `<div class="status status-info show">
                    <div class="loading">
                        <div class="spinner"></div>
                        <span>Scraping in progress</span>
                    </div>
                </div>`;
                
                statsHtml = `
                    <div class="stat">
                        <div class="stat-value">${formatNumber(data.scanned)}</div>
                        <div class="stat-label">Scanned</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value">${formatNumber(data.new)}</div>
                        <div class="stat-label">New</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value">${formatNumber(data.updated)}</div>
                        <div class="stat-label">Updated</div>
                    </div>
                `;
            } else if (data.status === 'done') {
                statusHtml = `<div class="status status-success show">
                    ✓ Scraping completed successfully
                </div>`;
                
                statsHtml = `
                    <div class="stat">
                        <div class="stat-value">${formatNumber(data.scanned)}</div>
                        <div class="stat-label">Total Scanned</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value">${formatNumber(data.new)}</div>
                        <div class="stat-label">New Messages</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value">${formatNumber(data.updated)}</div>
                        <div class="stat-label">Updated</div>
                    </div>
                `;
                
                if (data.output_csv || data.output_jsonl || data.output_db) {
                    downloadsHtml = '<div style="margin-top: 1rem; font-weight: 500;">Downloads:</div>';
                    if (data.output_csv) {
                        downloadsHtml += `<a href="/download/${data.job_id}/csv" class="btn btn-primary" download>
                            <svg class="icon" viewBox="0 0 24 24">
                                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                                <polyline points="7 10 12 15 17 10"></polyline>
                                <line x1="12" y1="15" x2="12" y2="3"></line>
                            </svg>
                            CSV
                        </a>`;
                    }
                    if (data.output_jsonl) {
                        downloadsHtml += `<a href="/download/${data.job_id}/jsonl" class="btn btn-primary" download>
                            <svg class="icon" viewBox="0 0 24 24">
                                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                                <polyline points="7 10 12 15 17 10"></polyline>
                                <line x1="12" y1="15" x2="12" y2="3"></line>
                            </svg>
                            JSONL
                        </a>`;
                    }
                    if (data.output_db) {
                        downloadsHtml += `<a href="/download/${data.job_id}/db" class="btn btn-primary" download>
                            <svg class="icon" viewBox="0 0 24 24">
                                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                                <polyline points="7 10 12 15 17 10"></polyline>
                                <line x1="12" y1="15" x2="12" y2="3"></line>
                            </svg>
                            Database
                        </a>`;
                    }
                }
            } else if (data.status === 'error') {
                statusHtml = `<div class="status status-error show">
                    ✗ Error: ${data.message || 'Unknown error'}
                </div>`;
            }
            
            contentDiv.innerHTML = statusHtml;
            statsDiv.innerHTML = statsHtml;
            downloadsDiv.innerHTML = downloadsHtml;
        }
        
        async function loadDatabases() {
            const listDiv = document.getElementById('databases-list');
            listDiv.innerHTML = '<div class="loading"><div class="spinner"></div><span>Loading...</span></div>';
            
            try {
                const response = await fetch('/api/databases');
                const data = await response.json();
                
                if (data.databases && data.databases.length > 0) {
                    let html = '<div style="display: grid; gap: 0.75rem;">';
                    for (const db of data.databases) {
                        html += `
                            <div style="display: flex; justify-content: space-between; align-items: center; padding: 1rem; background: var(--bg-tertiary); border-radius: 10px; border: 1px solid var(--border);">
                                <div>
                                    <div style="font-weight: 500;">${db}</div>
                                    <div style="font-size: 0.875rem; color: var(--text-secondary); margin-top: 0.25rem;">
                                        <a href="#" onclick="showStats('${db}'); return false;" style="color: var(--accent); text-decoration: none;">View Stats</a>
                                    </div>
                                </div>
                                <button class="btn btn-secondary" onclick="deleteDatabase('${db}')" style="padding: 0.5rem 1rem; font-size: 0.875rem;">
                                    <svg class="icon" viewBox="0 0 24 24" style="width: 16px; height: 16px;">
                                        <polyline points="3 6 5 6 21 6"></polyline>
                                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                                    </svg>
                                </button>
                            </div>
                        `;
                    }
                    html += '</div>';
                    listDiv.innerHTML = html;
                } else {
                    listDiv.innerHTML = '<div style="color: var(--text-secondary); text-align: center; padding: 2rem;">No databases found</div>';
                }
            } catch (error) {
                listDiv.innerHTML = `<div class="status status-error show">Error: ${error.message}</div>`;
            }
        }
        
        async function showStats(dbName) {
            try {
                const response = await fetch(`/api/stats/${encodeURIComponent(dbName)}`);
                const data = await response.json();
                alert(`Database: ${dbName}\\nTotal Messages: ${formatNumber(data.total_messages || 0)}\\nEarliest: ${data.earliest_date || 'N/A'}\\nLatest: ${data.latest_date || 'N/A'}`);
            } catch (error) {
                alert('Error loading stats: ' + error.message);
            }
        }
        
        async function deleteDatabase(dbName) {
            if (!confirm(`Archive database "${dbName}"? It will be kept for 3 months before permanent deletion.`)) {
                return;
            }
            
            try {
                const response = await fetch(`/api/delete/${encodeURIComponent(dbName)}`, {
                    method: 'DELETE'
                });
                
                if (response.ok) {
                    loadDatabases();
                } else {
                    const data = await response.json();
                    alert('Error: ' + (data.detail || 'Unknown error'));
                }
            } catch (error) {
                alert('Error: ' + error.message);
            }
        }
        
        function showStatus(elementId, message, type) {
            const div = document.getElementById(elementId);
            div.className = `status status-${type} show`;
            div.textContent = message;
            div.style.whiteSpace = 'pre-line';
        }
        
        function formatNumber(num) {
            return num.toString().replace(/\\B(?=(\\d{3})+(?!\\d))/g, ',');
        }
        
        // Load databases on page load
        loadDatabases();
    </script>
</body>
</html>
    """


@app.get("/test-dark-mode")
def test_dark_mode():
    return {"status": "NEW DARK MODE CODE IS RUNNING", "version": APP_VERSION}


@app.get("/health")
def health_check():
    """Health check endpoint for monitoring."""
    try:
        system_health = check_system_health()
        return {
            "status": "healthy",
            "version": APP_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "system": system_health
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}", exc_info=True)
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }


@app.get("/health/database/{db_name:path}")
def health_check_database(db_name: str):
    """Check health of a specific database."""
    if ".." in db_name or db_name.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid database path")
    
    return check_database_health(db_name)


