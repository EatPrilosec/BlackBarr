import os
import re
import hashlib
import asyncio
import logging
import json
from collections import Counter
from typing import Optional, Tuple, Dict, Any, List

from database import get_media_by_path, upsert_media, get_config

logger = logging.getLogger("blackbarr.scanner")

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm", ".ts", ".m2ts", ".vob", ".flv", ".wmv", ".iso"
}

class CropScanner:
    def __init__(self, media_dir: str = "/media"):
        self.media_dir = media_dir
        self.is_scanning = False
        self.total_files = 0
        self.scanned_files = 0
        self.current_file = ""
        self._stop_event = asyncio.Event()

    def compute_fast_hash(self, file_path: str, size: int, mtime: float) -> str:
        data = f"{file_path}:{size}:{mtime}"
        return hashlib.md5(data.encode("utf-8")).hexdigest()

    async def probe_media(self, file_path: str) -> Tuple[float, int, int, bool]:
        """
        Runs ffprobe to retrieve duration (seconds), width, height, and HDR status.
        """
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            file_path
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning(f"ffprobe failed for {file_path}: {stderr.decode()}")
                return 0.0, 0, 0, False

            data = json.loads(stdout.decode("utf-8", errors="ignore"))
            duration = float(data.get("format", {}).get("duration", 0.0))
            
            width, height = 0, 0
            is_hdr = False

            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    width = int(stream.get("width", 0))
                    height = int(stream.get("height", 0))
                    
                    color_transfer = stream.get("color_transfer", "").lower()
                    color_space = stream.get("color_space", "").lower()
                    pix_fmt = stream.get("pix_fmt", "").lower()

                    if color_transfer in ["smpte2084", "arib-std-b67"] or "bt2020" in color_space or "10le" in pix_fmt:
                        is_hdr = True
                    break

            return duration, width, height, is_hdr
        except Exception as e:
            logger.error(f"Error running ffprobe on {file_path}: {e}")
            return 0.0, 0, 0, False

    async def detect_crop_for_timestamp(self, file_path: str, timestamp_sec: float, limit: str) -> Optional[Tuple[int, int, int, int]]:
        """
        Runs ffmpeg cropdetect on a specific timestamp and returns parsed (w, h, x, y).
        """
        cmd = [
            "ffmpeg",
            "-ss", str(int(timestamp_sec)),
            "-i", file_path,
            "-vframes", "20",
            "-vf", f"cropdetect=limit={limit}:round=2",
            "-f", "null",
            "-"
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            output = stderr.decode("utf-8", errors="ignore")

            # Match crop=w:h:x:y lines in ffmpeg output
            matches = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", output)
            if matches:
                # Return the last detected crop in this sample window
                w, h, x, y = matches[-1]
                return int(w), int(h), int(x), int(y)
        except Exception as e:
            logger.debug(f"Cropdetect failed at timestamp {timestamp_sec} for {file_path}: {e}")
        return None

    async def analyze_file(self, file_path: str, sdr_limit: str, hdr_limit: str, sample_count: int = 10) -> Tuple[Optional[str], bool, str]:
        """
        Analyzes media file and returns (crop_val, is_hdr, status).
        """
        duration, orig_w, orig_h, is_hdr = await self.probe_media(file_path)
        if orig_w == 0 or orig_h == 0:
            return None, is_hdr, "ERROR"

        # Parse luma limit (e.g. if limit is 0.05 scale to 255 -> 13, else int)
        def _format_limit(l_val: str) -> str:
            try:
                f = float(l_val)
                if 0 < f < 1.0:
                    return str(max(1, int(f * 255)))
                return str(int(f))
            except Exception:
                return "24"

        limit = _format_limit(hdr_limit if is_hdr else sdr_limit)

        # Sample timestamps starting at +5 minutes (300 seconds) if duration permits
        start_time = 300.0 if duration > 600.0 else (duration * 0.1)
        end_time = duration - 300.0 if duration > 900.0 else (duration * 0.9)

        if end_time <= start_time:
            timestamps = [duration / 2.0]
        else:
            step = (end_time - start_time) / max(1, sample_count - 1)
            timestamps = [start_time + i * step for i in range(sample_count)]

        crop_samples: List[Tuple[int, int, int, int]] = []
        for ts in timestamps:
            crop_res = await self.detect_crop_for_timestamp(file_path, ts, limit)
            if crop_res:
                crop_samples.append(crop_res)

        if not crop_samples:
            # Couldn't detect or no crop lines found
            return None, is_hdr, "PROCESSED"

        # Find consensus crop mode
        counter = Counter(crop_samples)
        most_common_crop, count = counter.most_common(1)[0]
        w, h, x, y = most_common_crop

        # Check if crop is virtually full frame or starts at (0,0) with no top/left letterboxing
        if (abs(w - orig_w) <= 8 and abs(h - orig_h) <= 8) or (x <= 4 and y <= 4):
            # No letterbox/pillarbox offsets detected (already active picture or container-cropped)
            return None, is_hdr, "PROCESSED"
        else:
            crop_str = f"{w}:{h}:{x}:{y}"
            return crop_str, is_hdr, "PROCESSED"

    async def scan_file(self, file_path: str, force: bool = False):
        try:
            stat = os.stat(file_path)
            size = stat.st_size
            mtime = stat.st_mtime
        except Exception as e:
            logger.error(f"Cannot stat file {file_path}: {e}")
            return

        fast_hash = self.compute_fast_hash(file_path, size, mtime)
        existing = await get_media_by_path(file_path)

        if not force and existing:
            if existing["file_hash"] == fast_hash and existing["status"] in ["PROCESSED", "SKIPPED"]:
                logger.debug(f"Skipping unchanged file: {file_path}")
                return

        logger.info(f"Scanning media file: {file_path}")
        self.current_file = file_path

        sdr_limit = await get_config("sdr_crop_limit", "24")
        hdr_limit = await get_config("hdr_crop_limit", "0.05")
        sample_count = int(await get_config("sample_count", "10"))

        try:
            crop_val, is_hdr, status = await self.analyze_file(file_path, sdr_limit, hdr_limit, sample_count)
            await upsert_media(
                file_path=file_path,
                file_hash=fast_hash,
                file_size=size,
                mtime=mtime,
                is_hdr=is_hdr,
                crop_val=crop_val,
                status=status
            )
            logger.info(f"Scan complete for {file_path} -> crop_val: {crop_val}, status: {status}")
        except Exception as e:
            logger.error(f"Error during crop analysis of {file_path}: {e}")
            await upsert_media(
                file_path=file_path,
                file_hash=fast_hash,
                file_size=size,
                mtime=mtime,
                is_hdr=False,
                crop_val=None,
                status="ERROR"
            )

    async def run_scan(self, force: bool = False, directories: Optional[List[str]] = None):
        if self.is_scanning:
            logger.warning("Scan already in progress.")
            return

        if not directories:
            from database import get_scan_directories
            directories = await get_scan_directories()

        self._stop_event.clear()
        self.is_scanning = True
        self.scanned_files = 0
        self.total_files = 0
        logger.info(f"Starting library scan across directories: {directories}")

        def _collect_video_files(dirs: List[str]) -> List[str]:
            files_list = []
            for media_dir in dirs:
                if os.path.exists(media_dir):
                    for root, _, files in os.walk(media_dir):
                        for f in files:
                            ext = os.path.splitext(f)[1].lower()
                            if ext in VIDEO_EXTENSIONS:
                                files_list.append(os.path.join(root, f))
            return files_list

        video_files = await asyncio.to_thread(_collect_video_files, directories)

        self.total_files = len(video_files)
        logger.info(f"Found {self.total_files} video files to process across {len(directories)} directories.")

        for fpath in video_files:
            if self._stop_event.is_set():
                logger.info("Scan stopped by user request.")
                break
            await self.scan_file(fpath, force=force)
            self.scanned_files += 1

        self.is_scanning = False
        self.current_file = ""
        logger.info("Library scan finished.")

    async def scan_items_by_ids(self, media_ids: List[int]):
        if self.is_scanning:
            logger.warning("Scan already in progress.")
            return

        from database import get_db
        async with get_db() as db:
            placeholders = ",".join(["?"] * len(media_ids))
            async with db.execute(f"SELECT file_path FROM media_files WHERE id IN ({placeholders})", media_ids) as cursor:
                rows = await cursor.fetchall()
                file_paths = [r["file_path"] for r in rows]

        if not file_paths:
            return

        self._stop_event.clear()
        self.is_scanning = True
        self.scanned_files = 0
        self.total_files = len(file_paths)
        logger.info(f"Starting targeted scan for {self.total_files} items.")

        for fpath in file_paths:
            if self._stop_event.is_set():
                logger.info("Targeted ID scan stopped by user request.")
                break
            await self.scan_file(fpath, force=True)
            self.scanned_files += 1

        self.is_scanning = False
        self.current_file = ""
        logger.info("Targeted ID scan finished.")

    async def scan_by_filter(self, search: str = "", status: str = "", is_hdr: Optional[bool] = None, path_prefix: str = ""):
        if self.is_scanning:
            logger.warning("Scan already in progress.")
            return

        from database import list_media
        items, total = await list_media(search=search, status=status, is_hdr=is_hdr, path_prefix=path_prefix, limit=10000, offset=0)
        file_paths = [item["file_path"] for item in items]

        if not file_paths:
            return

        self._stop_event.clear()
        self.is_scanning = True
        self.scanned_files = 0
        self.total_files = len(file_paths)
        logger.info(f"Starting filtered scan for {self.total_files} items.")

        for fpath in file_paths:
            if self._stop_event.is_set():
                logger.info("Filtered scan stopped by user request.")
                break
            await self.scan_file(fpath, force=True)
            self.scanned_files += 1

        self.is_scanning = False
        self.current_file = ""
        logger.info("Filtered scan finished.")

    def stop_scan(self):
        if self.is_scanning:
            self._stop_event.set()
            logger.info("Scan cancellation event set.")


    def get_progress(self) -> Dict[str, Any]:
        return {
            "is_scanning": self.is_scanning,
            "total_files": self.total_files,
            "scanned_files": self.scanned_files,
            "current_file": self.current_file
        }

scanner_instance = CropScanner()
