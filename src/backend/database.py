import os
import aiosqlite
import logging
import time

logger = logging.getLogger("blackbarr.database")

DB_PATH = os.getenv("BLACKBARR_DB_PATH", "/config/BlackBarr.db")

async def get_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL;")
    await db.execute("PRAGMA busy_timeout=5000;")
    return db

async def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    async with await get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS media_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT UNIQUE NOT NULL,
                file_hash TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                mtime REAL NOT NULL,
                is_hdr INTEGER NOT NULL DEFAULT 0,
                crop_val TEXT DEFAULT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_media_files_path ON media_files(file_path);
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_media_files_status ON media_files(status);
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # Default configs
        defaults = {
            "force_transcode_enabled": "true",
            "target_server_url": os.getenv("TARGET_SERVER_URL", "http://localhost:8096"),
            "target_emby_url": os.getenv("TARGET_EMBY_URL", ""),
            "scan_directories": os.getenv("SCAN_DIRECTORIES", os.getenv("MEDIA_DIR", "/media")),
            "sdr_crop_limit": "24",
            "hdr_crop_limit": "0.05",
            "sample_count": "10",
            "scan_interval_minutes": "60",
            "auto_scan_on_startup": "true"
        }

        for k, v in defaults.items():
            await db.execute("""
                INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)
            """, (k, v))

        await db.commit()
        logger.info(f"Database initialized at {DB_PATH}")

async def get_scan_directories() -> list[str]:
    raw = await get_config("scan_directories", "/media")
    if not raw:
        return ["/media"]
    # Handle comma-separated or JSON list
    if raw.strip().startswith("["):
        import json
        try:
            res = json.loads(raw)
            if isinstance(res, list):
                return [str(d).strip() for d in res if str(d).strip()]
        except Exception:
            pass
    return [d.strip() for d in raw.split(",") if d.strip()]


async def get_config(key: str, default: str = "") -> str:
    async with await get_db() as db:
        async with db.execute("SELECT value FROM config WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return row["value"]
            return default

async def set_config(key: str, value: str):
    async with await get_db() as db:
        await db.execute("""
            INSERT INTO config (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (key, value))
        await db.commit()

async def get_all_config() -> dict:
    async with await get_db() as db:
        async with db.execute("SELECT key, value FROM config") as cursor:
            rows = await cursor.fetchall()
            return {row["key"]: row["value"] for row in rows}

async def get_media_by_path(file_path: str):
    async with await get_db() as db:
        async with db.execute("SELECT * FROM media_files WHERE file_path = ?", (file_path,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def upsert_media(file_path: str, file_hash: str, file_size: int, mtime: float, is_hdr: bool, crop_val: str | None, status: str):
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    async with await get_db() as db:
        await db.execute("""
            INSERT INTO media_files (file_path, file_hash, file_size, mtime, is_hdr, crop_val, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                file_hash = excluded.file_hash,
                file_size = excluded.file_size,
                mtime = excluded.mtime,
                is_hdr = excluded.is_hdr,
                crop_val = excluded.crop_val,
                status = excluded.status,
                updated_at = excluded.updated_at
        """, (file_path, file_hash, file_size, mtime, 1 if is_hdr else 0, crop_val, status, now, now))
        await db.commit()

async def list_media(search: str = "", status: str = "", limit: int = 100, offset: int = 0):
    async with await get_db() as db:
        query = "SELECT * FROM media_files WHERE 1=1"
        params = []
        if search:
            query += " AND file_path LIKE ?"
            params.append(f"%{search}%")
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            items = [dict(row) for row in rows]

        count_query = "SELECT COUNT(*) as count FROM media_files WHERE 1=1"
        count_params = []
        if search:
            count_query += " AND file_path LIKE ?"
            count_params.append(f"%{search}%")
        if status:
            count_query += " AND status = ?"
            count_params.append(status)

        async with db.execute(count_query, count_params) as cursor:
            count_row = await cursor.fetchone()
            total = count_row["count"] if count_row else 0

        return items, total

async def get_stats():
    async with await get_db() as db:
        async with db.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN crop_val IS NOT NULL AND crop_val != '' AND status = 'PROCESSED' THEN 1 ELSE 0 END) as cropped,
                SUM(CASE WHEN (crop_val IS NULL OR crop_val = '') AND status = 'PROCESSED' THEN 1 ELSE 0 END) as no_black_bars,
                SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status = 'ERROR' THEN 1 ELSE 0 END) as error,
                SUM(CASE WHEN status = 'SKIPPED' THEN 1 ELSE 0 END) as skipped
            FROM media_files
        """) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else {
                "total": 0, "cropped": 0, "no_black_bars": 0, "pending": 0, "error": 0, "skipped": 0
            }

async def delete_media(media_id: int):
    async with await get_db() as db:
        await db.execute("DELETE FROM media_files WHERE id = ?", (media_id,))
        await db.commit()
