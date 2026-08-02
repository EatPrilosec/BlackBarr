#!/usr/bin/env bash
set -e

echo "=== Starting BlackBarr Media Pipeline Middleware ==="

DB_DIR="${BLACKBARR_DB_DIR:-/config}"
mkdir -p "$DB_DIR"
chmod 755 "$DB_DIR"

WRAPPER_SRC="/usr/local/bin/ffmpeg-wrapper.sh"

# -------------------------------------------------------------
# Auto-Injection for Jellyfin / Emby target configurations
# -------------------------------------------------------------
auto_inject_config() {
    local target_dir="$1"
    local server_name="$2"

    if [ -d "$target_dir" ]; then
        echo "[BlackBarr] Found target $server_name config directory at $target_dir"
        
        # Copy wrapper to target directory so container/host volumes share execution path
        local target_wrapper="$target_dir/ffmpeg-wrapper.sh"
        cp -f "$WRAPPER_SRC" "$target_wrapper"
        chmod +x "$target_wrapper"
        echo "[BlackBarr] Copied wrapper script to $target_wrapper"

        # Auto-update encoding.xml or system.xml if present
        for config_file in "$target_dir/encoding.xml" "$target_dir/system.xml" "$target_dir/config/encoding.xml" "$target_dir/config/system.xml"; do
            if [ -f "$config_file" ]; then
                echo "[BlackBarr] Updating $config_file..."
                # Create backup if not already backed up
                if [ ! -f "${config_file}.bak" ]; then
                    cp "$config_file" "${config_file}.bak"
                    echo "[BlackBarr] Backed up original config to ${config_file}.bak"
                fi

                # Update EncoderAppPath or FfmpegPath xml tags
                sed -i 's|<EncoderAppPath>.*</EncoderAppPath>|<EncoderAppPath>'${target_wrapper}'</EncoderAppPath>|g' "$config_file" || true
                sed -i 's|<FfmpegPath>.*</FfmpegPath>|<FfmpegPath>'${target_wrapper}'</FfmpegPath>|g' "$config_file" || true
                echo "[BlackBarr] Successfully updated $config_file to point to $target_wrapper"
            fi
        done
    fi
}

auto_inject_config "/target-jellyfin-config" "Jellyfin"
auto_inject_config "/target-emby-config" "Emby"

# -------------------------------------------------------------
# Launch Uvicorn / FastAPI Backend
# -------------------------------------------------------------
PORT="${PORT:-6795}"
export PYTHONPATH="/app/src/backend:$PYTHONPATH"

echo "[BlackBarr] Starting BlackBarr servers (Web UI: 6795, Jellyfin Proxy: 6796, Emby Proxy: 6797)..."
exec python3 /app/src/backend/main.py
