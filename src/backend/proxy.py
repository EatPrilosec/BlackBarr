import logging
import json
import os
import asyncio
from typing import Optional
from fastapi import Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, RedirectResponse
import httpx
import websockets

from database import get_config, get_media_by_path, get_db

logger = logging.getLogger("blackbarr.proxy")

# follow_redirects=False is CRITICAL for reverse proxies to return 301/302 redirects to client browsers
client = httpx.AsyncClient(timeout=None, follow_redirects=False)

async def check_item_requires_cropping(item_id: Optional[str] = None, file_path: Optional[str] = None) -> bool:
    """
    Checks if a media file associated with item_id or file_path requires dynamic cropping.
    """
    if file_path:
        item = await get_media_by_path(file_path)
        if item and item.get("status") == "PROCESSED" and item.get("crop_val"):
            return True
        return False

    if item_id:
        async with get_db() as db:
            async with db.execute(
                "SELECT crop_val, status FROM media_files WHERE file_path LIKE ? OR file_path LIKE ?", 
                (f"%{item_id}%", f"%/{item_id}.%")
            ) as cursor:
                row = await cursor.fetchone()
                if row and row["status"] == "PROCESSED" and row["crop_val"]:
                    return True

            async with db.execute("SELECT file_path, crop_val FROM media_files WHERE status = 'PROCESSED' AND crop_val IS NOT NULL AND crop_val != ''") as cursor:
                rows = await cursor.fetchall()
                for row in rows:
                    base = os.path.basename(row["file_path"])
                    if item_id in base:
                        return True

    return False

def mutate_playback_info_request_payload(body_bytes: bytes) -> bytes:
    """
    Mutates incoming PlaybackInfo JSON payload to disable direct play/stream and preserve maximum bitrate for Emby/Jellyfin.
    """
    try:
        if not body_bytes:
            return body_bytes
        data = json.loads(body_bytes.decode("utf-8"))
        data["EnableDirectPlay"] = False
        data["EnableDirectStream"] = False
        data["EnableTranscoding"] = True
        data["AllowVideoStreamCopy"] = False
        data["AllowAudioStreamCopy"] = True
        
        # Override client-side 4Mbps bitrate limit for high-quality transcoding
        data["MaxStreamingBitrate"] = 140000000
        
        if "DeviceProfile" in data and isinstance(data["DeviceProfile"], dict):
            dp = data["DeviceProfile"]
            dp["DirectPlayProfiles"] = []
            dp["MaxStreamingBitrate"] = 140000000
        
        return json.dumps(data).encode("utf-8")
    except Exception as e:
        logger.warning(f"Failed to parse or mutate incoming PlaybackInfo body: {e}")
        return body_bytes

def mutate_playback_info_response_payload(body_bytes: bytes) -> bytes:
    """
    Mutates outgoing PlaybackInfo JSON response to force Transcode play method.
    """
    try:
        data = json.loads(body_bytes.decode("utf-8"))
        
        if "MediaSources" in data and isinstance(data["MediaSources"], list):
            for ms in data["MediaSources"]:
                ms["SupportsDirectPlay"] = False
                ms["SupportsDirectStream"] = False
                ms["SupportsTranscoding"] = True
                ms["PlayMethod"] = "Transcode"
                
                if "DirectStreamUrl" in ms:
                    ms["DirectStreamUrl"] = None

        return json.dumps(data).encode("utf-8")
    except Exception as e:
        logger.warning(f"Failed to parse or mutate outgoing PlaybackInfo response: {e}")
        return body_bytes

async def proxy_to_target(request: Request, default_target: str, config_key: str, server_name: str) -> Response:
    path = request.url.path
    if path == "/ui" or path.startswith("/ui/"):
        web_port = os.getenv("PORT", os.getenv("WEB_PORT", "6795"))
        host_header = request.headers.get("host", "").split(":")[0] or "localhost"
        return RedirectResponse(url=f"http://{host_header}:{web_port}/ui")

    configured_url = await get_config(config_key, default_target)
    if not configured_url:
        return Response(content=f"BlackBarr Proxy Error: {server_name} target URL not configured", status_code=502)

    target_server = configured_url.rstrip("/")
    url = f"{target_server}{request.url.path}"
    if request.url.query:
        url += f"?{request.url.query}"

    headers = dict(request.headers)
    headers.pop("host", None)
    
    body = await request.body()
    is_playback_info = "PlaybackInfo" in path or "/Sessions" in path
    
    should_force = False
    item_id = None
    
    if is_playback_info:
        clean_path = path
        if clean_path.startswith("/emby"):
            clean_path = clean_path[5:]
        elif clean_path.startswith("/jellyfin"):
            clean_path = clean_path[9:]

        parts = clean_path.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "Items" and parts[2] == "PlaybackInfo":
            item_id = parts[1]
        
        if not item_id:
            item_id = request.query_params.get("ItemId") or request.query_params.get("itemId")
            
        force_all = await get_config("force_transcode_all", "false")
        force_cropped = await get_config("force_transcode_cropped", "true")

        if force_all.lower() == "true":
            should_force = True
        elif force_cropped.lower() == "true":
            should_force = await check_item_requires_cropping(item_id=item_id)
        else:
            should_force = False

    if is_playback_info and should_force:
        logger.info(f"[{server_name} Proxy] Intercepting PlaybackInfo for item {item_id or 'unknown'} to enforce transcode response")
        if body and request.method == "POST":
            body = mutate_playback_info_request_payload(body)
            headers.pop("content-length", None)

    try:
        req = client.build_request(
            method=request.method,
            url=url,
            headers=headers,
            content=body
        )
        
        resp = await client.send(req, stream=True, follow_redirects=False)

        out_headers = dict(resp.headers)

        # Rewrite Location header for redirects so browser receives relative path (e.g. /web/index.html)
        for loc_key in ["location", "Location"]:
            if loc_key in out_headers:
                loc = out_headers[loc_key]
                if loc.startswith(target_server):
                    out_headers[loc_key] = loc[len(target_server):] or "/"
                elif loc.startswith("http://") or loc.startswith("https://"):
                    from urllib.parse import urlparse
                    parsed = urlparse(loc)
                    out_headers[loc_key] = parsed.path + ("?" + parsed.query if parsed.query else "")

        out_headers.pop("transfer-encoding", None)
        out_headers.pop("content-encoding", None)  # Stripping content-encoding fixes black/grey blank page!
        out_headers.pop("content-security-policy", None)

        if is_playback_info and should_force and resp.status_code == 200:
            resp_body = await resp.aread()
            await resp.aclose()
            mutated_resp_body = mutate_playback_info_response_payload(resp_body)
            out_headers["content-length"] = str(len(mutated_resp_body))

            return Response(
                content=mutated_resp_body,
                status_code=resp.status_code,
                headers=out_headers,
                media_type=resp.headers.get("content-type")
            )

        if is_playback_info:
            out_headers.pop("content-length", None)

        return StreamingResponse(
            resp.aiter_raw(chunk_size=65536),
            status_code=resp.status_code,
            headers=out_headers,
            background=None
        )
    except Exception as e:
        logger.error(f"[{server_name} Proxy] Error proxying request to {url}: {e}")
        return Response(content=f"BlackBarr Proxy Error: {str(e)}", status_code=502)

async def proxy_websocket(websocket: WebSocket, default_target: str, config_key: str, server_name: str):
    await websocket.accept()
    configured_url = await get_config(config_key, default_target)
    if not configured_url:
        await websocket.close(code=1011, reason=f"{server_name} target URL not configured")
        return

    target_ws = configured_url.rstrip("/").replace("http://", "ws://").replace("https://", "wss://")
    path = websocket.url.path
    query = websocket.url.query
    ws_target_url = f"{target_ws}{path}"
    if query:
        ws_target_url += f"?{query}"

    try:
        async with websockets.connect(ws_target_url) as target_ws_conn:
            async def forward_client_to_target():
                try:
                    while True:
                        msg = await websocket.receive()
                        if "text" in msg:
                            await target_ws_conn.send(msg["text"])
                        elif "bytes" in msg:
                            await target_ws_conn.send(msg["bytes"])
                        elif msg.get("type") == "websocket.disconnect":
                            break
                except Exception:
                    pass

            async def forward_target_to_client():
                try:
                    async for msg in target_ws_conn:
                        if isinstance(msg, str):
                            await websocket.send_text(msg)
                        else:
                            await websocket.send_bytes(msg)
                except Exception:
                    pass

            await asyncio.gather(
                forward_client_to_target(),
                forward_target_to_client(),
                return_exceptions=True
            )
    except Exception as e:
        logger.warning(f"[{server_name} WebSocket Proxy] Connection error to {ws_target_url}: {e}")
        try:
            await websocket.close(code=1011)
        except Exception:
            pass

async def reverse_proxy_handler(request: Request) -> Response:
    default_jellyfin = os.getenv("TARGET_SERVER_URL", "http://localhost:8096")
    return await proxy_to_target(request, default_jellyfin, "target_server_url", "Jellyfin")

async def reverse_proxy_emby_handler(request: Request) -> Response:
    default_emby = os.getenv("TARGET_EMBY_URL", "http://localhost:8096")
    return await proxy_to_target(request, default_emby, "target_emby_url", "Emby")

