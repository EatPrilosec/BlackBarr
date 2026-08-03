#!/usr/bin/env bash
# BlackBarr FFmpeg Wrapper & Dynamic Crop Injector
set -e

DB_PATH="${BLACKBARR_DB_PATH:-/config/BlackBarr.db}"
# Common paths for Jellyfin and Emby FFmpeg
COMMON_PATHS=(
    "/usr/lib/jellyfin-ffmpeg/ffmpeg"
    "/bin/ffmpeg"
    "/usr/local/bin/ffmpeg"
    "/usr/bin/ffmpeg"
)

REAL_FFMPEG="${REAL_FFMPEG_PATH}"

if [ -z "$REAL_FFMPEG" ] || [ ! -x "$REAL_FFMPEG" ]; then
    for p in "${COMMON_PATHS[@]}"; do
        if [ -x "$p" ]; then
            REAL_FFMPEG="$p"
            break
        fi
    done
fi

# Fallback to system ffmpeg if custom path doesn't exist
if [ -z "$REAL_FFMPEG" ] || [ ! -x "$REAL_FFMPEG" ]; then
    if command -v ffmpeg >/dev/null 2>&1; then
        REAL_FFMPEG="$(command -v ffmpeg)"
    else
        echo "Error: Could not find real ffmpeg executable!" >&2
        exit 127
    fi
fi

ARGS=("$@")
INPUT_FILE=""
CROP_VAL=""

# Extract input file path from arguments (-i <filepath>)
for ((i=0; i<${#ARGS[@]}; i++)); do
    if [[ "${ARGS[i]}" == "-i" ]] && (( i + 1 < ${#ARGS[@]} )); then
        RAW_INPUT="${ARGS[i+1]}"
        
        # Strip 'file:' prefix if present
        if [[ "$RAW_INPUT" == file:* ]]; then
            RAW_INPUT="${RAW_INPUT#file:}"
        fi
        
        # Strip surrounding double and single quotes
        RAW_INPUT="${RAW_INPUT%\"}"
        RAW_INPUT="${RAW_INPUT#\"}"
        RAW_INPUT="${RAW_INPUT%\'}"
        RAW_INPUT="${RAW_INPUT#\'}"

        INPUT_FILE="$RAW_INPUT"

        # If it's a media file (not pipe or stream URL), save it
        if [[ -f "$INPUT_FILE" ]]; then
            break
        fi
    fi
done

# If input file was found, query BlackBarr API for crop value
if [[ -n "$INPUT_FILE" ]] && command -v curl >/dev/null 2>&1; then
    # Try 127.0.0.1 IPv4 loopback first, then localhost, then blackbarr bridge network
    RES=$(curl -G -s --data-urlencode "path=$INPUT_FILE" "http://127.0.0.1:6795/api/crop_val" || curl -G -s --data-urlencode "path=$INPUT_FILE" "http://localhost:6795/api/crop_val" || curl -G -s --data-urlencode "path=$INPUT_FILE" "http://blackbarr:6795/api/crop_val" || true)
    CROP_VAL=$(echo "$RES" | grep -o 'crop=[^"]*' || true)
    CROP_VAL="${CROP_VAL#crop=}"
fi

# If no crop value found in DB, run original ffmpeg command untouched
if [[ -z "$CROP_VAL" ]]; then
    exec "$REAL_FFMPEG" "$@"
fi

# Detect hardware acceleration type from arguments
HAS_QSV=0
HAS_CUDA=0
HAS_VAAPI=0

for arg in "$@"; do
    if [[ "$arg" == *"qsv"* ]]; then HAS_QSV=1; fi
    if [[ "$arg" == *"cuda"* ]] || [[ "$arg" == *"nvenc"* ]]; then HAS_CUDA=1; fi
    if [[ "$arg" == *"vaapi"* ]]; then HAS_VAAPI=1; fi
done

# Adapt crop / normalization filter for QSV, CUDA, or VAAPI hardware pipelines
if [[ "$CROP_VAL" == "setsar=1" ]]; then
    if (( HAS_VAAPI )); then
        CROP_FILTER="hwdownload,format=nv12,setsar=1,hwupload"
    else
        CROP_FILTER="setsar=1"
    fi
else
    if (( HAS_VAAPI )); then
        CROP_FILTER="hwdownload,format=nv12,crop=${CROP_VAL},setsar=1,hwupload"
    elif (( HAS_QSV )); then
        IFS=':' read -r CW CH CX CY <<< "$CROP_VAL"
        if [[ -n "$CW" ]] && [[ -n "$CH" ]]; then
            CROP_FILTER="vpp_qsv=crop_w=$CW:crop_h=$CH:crop_x=${CX:-0}:crop_y=${CY:-0},setsar=1"
        else
            CROP_FILTER="crop=${CROP_VAL},setsar=1"
        fi
    else
        CROP_FILTER="crop=${CROP_VAL},setsar=1"
    fi
fi

echo "[BlackBarr Wrapper] Intercepted $INPUT_FILE -> Injecting crop filter: $CROP_FILTER" >&2

# Build modified argument array
NEW_ARGS=()
FILTER_INJECTED=0

for ((i=0; i<${#ARGS[@]}; i++)); do
    arg="${ARGS[i]}"
    
    if [[ "$arg" == "-vf" ]] || [[ "$arg" == "-filter_complex" ]]; then
        NEW_ARGS+=("$arg")
        if (( i + 1 < ${#ARGS[@]} )); then
            ORIG_FILTER="${ARGS[i+1]}"
            # Check if filter starts with stream specifier like [0:0] or [0:v] for Emby
            if [[ "$ORIG_FILTER" =~ ^\[[0-9a-zA-Z:]+\] ]]; then
                SPEC="${BASH_REMATCH[0]}"
                NEW_FILTER="${ORIG_FILTER/"$SPEC"/"$SPEC"$CROP_FILTER,}"
                NEW_ARGS+=("$NEW_FILTER")
            else
                # Prepend crop filter to existing filter chain
                NEW_ARGS+=("${CROP_FILTER},${ORIG_FILTER}")
            fi
            i=$((i + 1))
            FILTER_INJECTED=1
        fi
    else
        NEW_ARGS+=("$arg")
    fi
done

# If no -vf or -filter_complex argument existed, inject -vf before output file or encoding params
if (( ! FILTER_INJECTED )); then
    FINAL_ARGS=()
    INJECTED_VF=0
    for arg in "${NEW_ARGS[@]}"; do
        if [[ "$arg" == "-c:v" ]] || [[ "$arg" == "-codec:v" ]] || [[ "$arg" == "-vcodec" ]]; then
            if (( ! INJECTED_VF )); then
                FINAL_ARGS+=("-vf" "$CROP_FILTER")
                INJECTED_VF=1
            fi
        fi
        FINAL_ARGS+=("$arg")
    done

    if (( ! INJECTED_VF )); then
        # Append before last argument (output file or pipe)
        LAST_IDX=$((${#NEW_ARGS[@]} - 1))
        FINAL_ARGS=()
        for ((k=0; k<${#NEW_ARGS[@]}; k++)); do
            if (( k == LAST_IDX )); then
                FINAL_ARGS+=("-vf" "$CROP_FILTER")
            fi
            FINAL_ARGS+=("${NEW_ARGS[k]}")
        done
    fi
    exec "$REAL_FFMPEG" "${FINAL_ARGS[@]}"
else
    exec "$REAL_FFMPEG" "${NEW_ARGS[@]}"
fi
