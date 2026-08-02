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

@router.get("/crop_val")
async def get_crop_for_file_safe(path: str = Query(..., description="File path to query crop value for")):
    async with get_db() as db:
        async with db.execute(
            "SELECT crop_val FROM media_files WHERE file_path = ? AND status = 'PROCESSED' AND crop_val IS NOT NULL AND crop_val != '' LIMIT 1",
            (path,)
        ) as cursor:
            row = await cursor.fetchone()
            if row and row["crop_val"]:
                from fastapi.responses import Response
                return Response(content=f"crop={row['crop_val']}", media_type="text/plain")
            
    from fastapi.responses import Response
    return Response(content="", media_type="text/plain")

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
    status: str = Query("", description="Filter by status (CROPPED, NO_BLACK_BARS, PENDING, ERROR, SKIPPED)"),
    is_hdr: Optional[bool] = Query(None, description="Filter by format (True for HDR, False for SDR)"),
    sort_by: str = Query("updated_at", description="Sort by column (file_path, is_hdr, crop_val, status, updated_at)"),
    sort_order: str = Query("desc", description="Sort order (asc or desc)"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0)
):
    items, total = await list_media(
        search=search,
        status=status,
        is_hdr=is_hdr,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset
    )
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "sort_by": sort_by,
        "sort_order": sort_order
    }

class ScanRequestModel(BaseModel):
    mode: str = "new"  # "new", "full", "selected", "filtered"
    media_ids: Optional[List[int]] = None
    search: Optional[str] = ""
    status: Optional[str] = ""
    is_hdr: Optional[bool] = None

@router.post("/scan")
async def trigger_scan(
    background_tasks: BackgroundTasks, 
    payload: Optional[ScanRequestModel] = None,
    force: bool = Query(False)
):
    if scanner_instance.is_scanning:
        raise HTTPException(status_code=400, detail="Scan is already in progress")
    
    if payload:
        if payload.mode == "selected" and payload.media_ids:
            background_tasks.add_task(scanner_instance.scan_items_by_ids, payload.media_ids)
            return {"message": f"Rescan initiated for {len(payload.media_ids)} items", "mode": "selected"}
        elif payload.mode == "filtered":
            background_tasks.add_task(
                scanner_instance.scan_by_filter, 
                search=payload.search or "", 
                status=payload.status or "", 
                is_hdr=payload.is_hdr
            )
            return {"message": "Filtered library rescan initiated", "mode": "filtered"}
        elif payload.mode == "full":
            background_tasks.add_task(scanner_instance.run_scan, force=True)
            return {"message": "Full library rescan initiated", "mode": "full"}
        else:
            background_tasks.add_task(scanner_instance.run_scan, force=False)
            return {"message": "Library scan for new/changed files initiated", "mode": "new"}
    
    # Fallback for query param (backward compatibility)
    background_tasks.add_task(scanner_instance.run_scan, force=force)
    return {"message": "Library scan initiated", "force": force}

@router.post("/media/{media_id}/rescan")
async def rescan_single_media_item(media_id: int):
    async with get_db() as db:
        async with db.execute("SELECT file_path FROM media_files WHERE id = ?", (media_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Media item not found")
            file_path = row["file_path"]

    await scanner_instance.scan_file(file_path, force=True)

    async with get_db() as db:
        async with db.execute("SELECT * FROM media_files WHERE id = ?", (media_id,)) as cursor:
            updated_row = await cursor.fetchone()
            return {"message": "Media item rescanned successfully", "item": dict(updated_row)}

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

class TestConnectionModel(BaseModel):
    url: str

@router.post("/test-connection")
async def test_server_connection(payload: TestConnectionModel):
    url = payload.url.strip().rstrip("/")
    if not url:
        raise HTTPException(status_code=400, detail="Server URL cannot be empty")

    test_endpoints = [
        f"{url}/System/Info/Public",
        f"{url}/System/Info",
        f"{url}/emby/System/Info/Public",
        f"{url}/jellyfin/System/Info/Public",
        f"{url}/"
    ]

    import httpx
    async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
        for ep in test_endpoints:
            try:
                resp = await client.get(ep)
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        server_name = data.get("ServerName") or data.get("ProductName") or "Media Server"
                        version = data.get("Version") or ""
                        ver_str = f" v{version}" if version else ""
                        return {
                            "success": True,
                            "message": f"Successfully connected to {server_name}{ver_str}",
                            "server_name": server_name,
                            "version": version
                        }
                    except Exception:
                        return {
                            "success": True,
                            "message": f"Successfully reached server at {url} (HTTP 200)"
                        }
            except Exception:
                continue

    return {
        "success": False,
        "message": f"Could not connect to media server at {url}. Verify URL and network status."
    }
