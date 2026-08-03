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

        # Ensure target_wrapper is a file, not an accidentally created directory
        local target_wrapper="$target_dir/ffmpeg-wrapper.sh"
        if [ -d "$target_wrapper" ]; then
            echo "[BlackBarr] Removing invalid directory at $target_wrapper..."
            rm -rf "$target_wrapper"
        fi
        cp -f "$WRAPPER_SRC" "$target_wrapper"
        chmod 755 "$target_wrapper"
        
        if [ -n "$dir_uid_gid" ]; then
            chown "$dir_uid_gid" "$target_wrapper" 2>/dev/null || true
        fi
        if [ "$server_name" = "Jellyfin" ] && [ -d "/app/plugins/jellyfin" ]; then
            local plugin_target_dir="$target_dir/plugins/BlackBarrHelper"
            mkdir -p "$plugin_target_dir"
            cp -rf /app/plugins/jellyfin/* "$plugin_target_dir/"
            if [ -n "$dir_uid_gid" ]; then
                chown -R "$dir_uid_gid" "$plugin_target_dir" 2>/dev/null || true
            fi
            echo "[BlackBarr] Installed/Updated Jellyfin plugin 'BlackBarr Helper' at $plugin_target_dir"
        fi
        if [ "$server_name" = "Emby" ]; then
            local target_emby_preinit="$target_dir/00-emby-preinit.sh"
            if [ -d "$target_emby_preinit" ]; then
                echo "[BlackBarr] Removing invalid directory at $target_emby_preinit..."
                rm -rf "$target_emby_preinit"
            fi
            cat << 'EOF' > "$target_emby_preinit"
#!/command/with-contenv sh

# BlackBarr Emby Container Pre-Init Hook
if [ ! -L /bin/ffmpeg ]; then
    if [ -f /bin/ffmpeg ]; then
        cp -f /bin/ffmpeg /bin/ffmpeg.real 2>/dev/null || true
        chmod +x /bin/ffmpeg.real 2>/dev/null || true
    fi
    ln -sf /config/ffmpeg-wrapper.sh /bin/ffmpeg
fi
EOF
            chmod 755 "$target_emby_preinit"
            if [ -n "$dir_uid_gid" ]; then
                chown "$dir_uid_gid" "$target_emby_preinit" 2>/dev/null || true
            fi
            echo "[BlackBarr] Generated Emby pre-init hook at $target_emby_preinit"
        fi
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
