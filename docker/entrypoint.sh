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
        
        # Determine target directory ownership (UID:GID)
        local dir_uid_gid
        dir_uid_gid=$(stat -c "%u:%g" "$target_dir" 2>/dev/null || true)

        # Copy wrapper script and make executable
        local target_wrapper="$target_dir/ffmpeg-wrapper.sh"
        cp -f "$WRAPPER_SRC" "$target_wrapper"
        chmod 755 "$target_wrapper"
        
        if [ -n "$dir_uid_gid" ]; then
            chown "$dir_uid_gid" "$target_wrapper" 2>/dev/null || true
        fi
        echo "[BlackBarr] Copied wrapper script to $target_wrapper (owner: ${dir_uid_gid:-default})"

        # Auto-update encoding.xml or system.xml if present
        for config_file in "$target_dir/encoding.xml" "$target_dir/system.xml" "$target_dir/config/encoding.xml" "$target_dir/config/system.xml"; do
            if [ -f "$config_file" ]; then
                echo "[BlackBarr] Updating $config_file..."
                
                # Capture original file ownership and permissions mode
                local file_uid_gid file_mode
                file_uid_gid=$(stat -c "%u:%g" "$config_file" 2>/dev/null || true)
                file_mode=$(stat -c "%a" "$config_file" 2>/dev/null || true)

                # Create backup preserving permissions & metadata if not already backed up
                if [ ! -f "${config_file}.bak" ]; then
                    cp -p "$config_file" "${config_file}.bak"
                    echo "[BlackBarr] Backed up original config to ${config_file}.bak"
                fi

                # Update EncoderAppPath or FfmpegPath xml tags
                sed -i 's|<EncoderAppPath>.*</EncoderAppPath>|<EncoderAppPath>'${target_wrapper}'</EncoderAppPath>|g' "$config_file" || true
                sed -i 's|<FfmpegPath>.*</FfmpegPath>|<FfmpegPath>'${target_wrapper}'</FfmpegPath>|g' "$config_file" || true

                # Restore original ownership and permissions mode after sed edit
                if [ -n "$file_uid_gid" ]; then
                    chown "$file_uid_gid" "$config_file" 2>/dev/null || true
                fi
                if [ -n "$file_mode" ]; then
                    chmod "$file_mode" "$config_file" 2>/dev/null || true
                fi

                echo "[BlackBarr] Successfully updated $config_file (owner: ${file_uid_gid:-default}, mode: ${file_mode:-default})"
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
