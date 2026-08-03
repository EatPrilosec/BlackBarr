import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response

from database import init_db, get_config
from api import router as api_router
from proxy import reverse_proxy_handler, reverse_proxy_emby_handler, proxy_websocket
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

from fastapi.middleware.cors import CORSMiddleware

# Dedicated FastAPI app for Web UI & Management REST API (Port 6795)
app = FastAPI(title="BlackBarr Management Dashboard", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)

@app.get("/health")
@app.get("/api/health")
async def health_check():
    """
    Healthcheck endpoint for Docker health checks.
    """
    return {"status": "healthy"}

FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend"))

@app.middleware("http")
async def web_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        return Response(status_code=200)

    path = request.url.path
    if path.startswith("/api"):
        return await call_next(request)

    file_name = path.lstrip("/")
    if not file_name or file_name == "ui" or path.startswith("/ui"):
        file_name = "index.html"

    target = os.path.join(FRONTEND_DIR, file_name)
    if os.path.exists(target) and os.path.isfile(target):
        return FileResponse(target)

    index_file = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)

    return await call_next(request)

def make_dual_stack_socket(port: int):
    import socket
    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    except Exception:
        pass
    sock.bind(("::", port))
    return sock

async def start_servers():
    import uvicorn
    port_web = int(os.getenv("PORT", os.getenv("WEB_PORT", "6795")))

    config_web = uvicorn.Config(app, log_level="info")
    server_web = uvicorn.Server(config_web)
    sock_web = make_dual_stack_socket(port_web)

    logger.info(f"Starting BlackBarr Web Management UI on port {port_web} (Dual-Stack IPv4/IPv6)")
    await server_web.serve(sockets=[sock_web])

if __name__ == "__main__":
    asyncio.run(start_servers())
