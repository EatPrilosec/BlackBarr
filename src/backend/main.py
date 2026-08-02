import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response

from database import init_db, get_config
from api import router as api_router
from proxy import reverse_proxy_handler, reverse_proxy_emby_handler
from scanner import scanner_instance

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("blackbarr.main")

async def periodic_scanner_loop():
    while True:
        try:
            interval_str = await get_config("scan_interval_minutes", "60")
            interval_mins = max(5, int(interval_str))
            await asyncio.sleep(interval_mins * 60)
            if not scanner_instance.is_scanning:
                logger.info("Executing scheduled library crop scan...")
                await scanner_instance.run_scan(force=False)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in periodic scanner loop: {e}")
            await asyncio.sleep(60)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing BlackBarr database...")
    await init_db()

    auto_scan = await get_config("auto_scan_on_startup", "true")

    scan_task = None
    if auto_scan.lower() == "true":
        logger.info("Auto-scan on startup enabled. Starting scan task...")
        asyncio.create_task(scanner_instance.run_scan(force=False))

    loop_task = asyncio.create_task(periodic_scanner_loop())

    yield

    loop_task.cancel()
    logger.info("BlackBarr server shutting down.")

# Dedicated FastAPI app for Web UI & Management REST API (Port 6795)
app = FastAPI(title="BlackBarr Management Dashboard", lifespan=lifespan)
app.include_router(api_router)

FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend"))

@app.get("/", include_in_schema=False)
@app.get("/ui", include_in_schema=False)
@app.get("/ui/{file_path:path}", include_in_schema=False)
async def serve_ui_root(file_path: str = ""):
    if not file_path or file_path == "/":
        file_path = "index.html"
    target = os.path.join(FRONTEND_DIR, file_path)
    if os.path.exists(target) and os.path.isfile(target):
        return FileResponse(target)
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

@app.middleware("http")
async def web_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api") or path in ["/", "/index.html", "/app.js", "/style.css", "/favicon.ico"] or path.startswith("/ui"):
        target = os.path.join(FRONTEND_DIR, path.lstrip("/"))
        if os.path.exists(target) and os.path.isfile(target):
            return FileResponse(target)
        return await call_next(request)
    return await call_next(request)

# Dedicated FastAPI app for Jellyfin Proxy (Port 6796)
jf_app = FastAPI(title="BlackBarr Dedicated Jellyfin Proxy")

@jf_app.middleware("http")
async def jf_routing_middleware(request: Request, call_next):
    return await reverse_proxy_handler(request)

# Dedicated FastAPI app for Emby Proxy (Port 6797)
emby_app = FastAPI(title="BlackBarr Dedicated Emby Proxy")

@emby_app.middleware("http")
async def emby_routing_middleware(request: Request, call_next):
    return await reverse_proxy_emby_handler(request)

async def start_servers():
    import uvicorn
    port_web = int(os.getenv("PORT", os.getenv("WEB_PORT", "6795")))
    port_jf = int(os.getenv("JELLYFIN_PORT", "6796"))
    port_emby = int(os.getenv("EMBY_PORT", "6797"))

    config_web = uvicorn.Config(app, host="0.0.0.0", port=port_web, log_level="info")
    config_jf = uvicorn.Config(jf_app, host="0.0.0.0", port=port_jf, log_level="info")
    config_emby = uvicorn.Config(emby_app, host="0.0.0.0", port=port_emby, log_level="info")

    server_web = uvicorn.Server(config_web)
    server_jf = uvicorn.Server(config_jf)
    server_emby = uvicorn.Server(config_emby)

    logger.info(f"Starting BlackBarr Web Management UI on port {port_web}")
    logger.info(f"Starting BlackBarr Dedicated Jellyfin Proxy on port {port_jf}")
    logger.info(f"Starting BlackBarr Dedicated Emby Proxy on port {port_emby}")

    await asyncio.gather(
        server_web.serve(),
        server_jf.serve(),
        server_emby.serve()
    )

if __name__ == "__main__":
    asyncio.run(start_servers())
