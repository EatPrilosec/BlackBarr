import asyncio
import logging
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from pydantic import BaseModel

from database import (
    list_media, upsert_media, delete_media, get_stats, 
    get_all_config, set_config, get_media_by_path, get_db
)
from scanner import scanner_instance

logger = logging.getLogger("blackbarr.api")

router = APIRouter(prefix="/api")

class ConfigUpdateModel(BaseModel):
    configs: Dict[str, str]

class MediaItemUpdateModel(BaseModel):
    crop_val: Optional[str] = None
    status: Optional[str] = None
    is_hdr: Optional[bool] = None

class MediaItemCreateModel(BaseModel):
    file_path: str
    crop_val: Optional[str] = None
    status: str = "PROCESSED"
    is_hdr: bool = False

@router.get("/media")
async def get_media_list(
    search: str = Query("", description="Filter by file path"),
    status: str = Query("", description="Filter by status (PROCESSED, PENDING, ERROR, SKIPPED)"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0)
):
    items, total = await list_media(search=search, status=status, limit=limit, offset=offset)
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset
    }

@router.post("/scan")
async def trigger_scan(background_tasks: BackgroundTasks, force: bool = Query(False)):
    if scanner_instance.is_scanning:
        raise HTTPException(status_code=400, detail="Scan is already in progress")
    
    background_tasks.add_task(scanner_instance.run_scan, force=force)
    return {"message": "Library scan initiated", "force": force}

@router.get("/scan/status")
async def get_scan_status():
    return scanner_instance.get_progress()

@router.put("/media/{media_id}")
async def update_media_item(media_id: int, payload: MediaItemUpdateModel):
    async with get_db() as db:
        async with db.execute("SELECT * FROM media_files WHERE id = ?", (media_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Media item not found")
            
            existing = dict(row)

    file_path = existing["file_path"]
    file_hash = existing["file_hash"]
    file_size = existing["file_size"]
    mtime = existing["mtime"]
    
    new_crop = payload.crop_val if payload.crop_val is not None else existing["crop_val"]
    new_status = payload.status if payload.status is not None else existing["status"]
    new_hdr = payload.is_hdr if payload.is_hdr is not None else bool(existing["is_hdr"])

    await upsert_media(
        file_path=file_path,
        file_hash=file_hash,
        file_size=file_size,
        mtime=mtime,
        is_hdr=new_hdr,
        crop_val=new_crop,
        status=new_status
    )

    return {"message": "Media item updated successfully", "id": media_id}

@router.post("/media")
async def create_media_item(payload: MediaItemCreateModel):
    fast_hash = f"manual_{hash(payload.file_path)}"
    await upsert_media(
        file_path=payload.file_path,
        file_hash=fast_hash,
        file_size=0,
        mtime=0.0,
        is_hdr=payload.is_hdr,
        crop_val=payload.crop_val,
        status=payload.status
    )
    return {"message": "Media item created successfully", "file_path": payload.file_path}

@router.delete("/media/{media_id}")
async def remove_media_item(media_id: int):
    await delete_media(media_id)
    return {"message": "Media item deleted successfully", "id": media_id}

@router.get("/stats")
async def get_dashboard_stats():
    return await get_stats()

@router.get("/config")
async def get_configuration():
    return await get_all_config()

@router.post("/config")
async def update_configuration(payload: ConfigUpdateModel):
    for k, v in payload.configs.items():
        await set_config(k, str(v))
    return {"message": "Configuration updated successfully", "configs": payload.configs}
