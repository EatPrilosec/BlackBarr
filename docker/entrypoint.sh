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
        if [ "$server_name" = "Emby" ]; then
            local target_emby_run="$target_dir/emby-server-run.sh"
            cat << 'EOF' > "$target_emby_run"
#!/command/with-contenv sh

# BlackBarr Emby Dynamic FFmpeg Wrapper Pre-Init Hook
if [ ! -L /bin/ffmpeg ]; then
    if [ -f /bin/ffmpeg ]; then
        cp -f /bin/ffmpeg /bin/ffmpeg.real 2>/dev/null || true
        chmod +x /bin/ffmpeg.real 2>/dev/null || true
    fi
    ln -sf /config/ffmpeg-wrapper.sh /bin/ffmpeg
fi

[ -n "$PUID" ] && UID="$PUID"
[ -n "$PGID" ] && GID="$PGID"
[ -n "$UMASK" ] && umask "$UMASK"

if [ "$(ls -nd /config | tr -s '[:space:]' | cut -d' ' -f3)" -ne "$UID" ] || [ "$(ls -nd /config | tr -s '[:space:]' | cut -d' ' -f4)" -ne "$GID" ]; then
  chown "$UID":"$GID" -R /config
fi

for d in $(find /dev/dri -type c 2>/dev/null); do
  gid=$(stat -c %g "${d}")
  [ -z "${GIDLIST}" ] && GIDLIST=${gid} || GIDLIST="${GIDLIST},${gid}"
done

exec s6-applyuidgid -U /system/EmbyServer \
    -programdata /config \
    -ffdetect /bin/ffdetect \
    -ffmpeg /bin/ffmpeg \
    -ffprobe /bin/ffprobe \
    -restartexitcode 3
EOF
            chmod 755 "$target_emby_run"
            if [ -n "$dir_uid_gid" ]; then
                chown "$dir_uid_gid" "$target_emby_run" 2>/dev/null || true
            fi
            echo "[BlackBarr] Generated Emby pre-init launcher at $target_emby_run"
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
