#!/usr/bin/env bash
# BlackBarr FFmpeg Wrapper & Dynamic Crop Injector
set -e

DB_PATH="${BLACKBARR_DB_PATH:-/config/BlackBarr.db}"
REAL_FFMPEG="${REAL_FFMPEG_PATH:-/usr/bin/ffmpeg}"

# Fallback to system ffmpeg if custom path doesn't exist
if [ ! -x "$REAL_FFMPEG" ]; then
    if command -v ffmpeg >/dev/null 2>&1; then
        REAL_FFMPEG="$(command -v ffmpeg)"
    fi
fi

ARGS=("$@")
INPUT_FILE=""
CROP_VAL=""

# Extract input file path from arguments (-i <filepath>)
for ((i=0; i<${#ARGS[@]}; i++)); do
    if [[ "${ARGS[i]}" == "-i" ]] && (( i + 1 < ${#ARGS[@]} )); then
        INPUT_FILE="${ARGS[i+1]}"
        # If it's a media file (not pipe or stream URL), save it
        if [[ -f "$INPUT_FILE" ]]; then
            break
        fi
    fi
done

# If input file was found and database exists, query SQLite for crop value
if [[ -n "$INPUT_FILE" ]] && [[ -f "$DB_PATH" ]] && command -v sqlite3 >/dev/null 2>&1; then
    # Escape single quotes for sqlite query
    ESCAPED_PATH=$(echo "$INPUT_FILE" | sed "s/'/''/g")
    CROP_VAL=$(sqlite3 "$DB_PATH" "SELECT crop_val FROM media_files WHERE file_path = '$ESCAPED_PATH' AND status = 'PROCESSED' AND crop_val IS NOT NULL AND crop_val != '' LIMIT 1;" 2>/dev/null || true)
fi

# If no crop value found in DB, run original ffmpeg command untouched
if [[ -z "$CROP_VAL" ]]; then
    exec "$REAL_FFMPEG" "$@"
fi

# Format crop filter string
# CROP_VAL can be "1920:800:0:140" or "crop=1920:800:0:140"
if [[ "$CROP_VAL" =~ ^crop= ]]; then
    CROP_FILTER="$CROP_VAL"
else
    CROP_FILTER="crop=$CROP_VAL"
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

# Adapt crop filter for QSV or CUDA hardware pipelines if needed
if (( HAS_QSV )); then
    # Parse w:h:x:y
    RAW_CROP="${CROP_FILTER#crop=}"
    IFS=':' read -r CW CH CX CY <<< "$RAW_CROP"
    if [[ -n "$CW" ]] && [[ -n "$CH" ]]; then
        QSV_CROP="vpp_qsv=crop_w=$CW:crop_h=$CH:crop_x=${CX:-0}:crop_y=${CY:-0}"
        CROP_FILTER="$QSV_CROP"
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
            # Prepend crop filter to existing filter chain
            NEW_ARGS+=("${CROP_FILTER},${ORIG_FILTER}")
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
