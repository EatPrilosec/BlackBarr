#!/bin/sh
# BlackBarr FFmpeg Wrapper & Dynamic Crop Injector (POSIX /bin/sh Compatible)

DB_PATH="${BLACKBARR_DB_PATH:-/config/BlackBarr.db}"

# Locate real ffmpeg executable
REAL_FFMPEG="${REAL_FFMPEG_PATH}"

if [ -z "$REAL_FFMPEG" ] || [ ! -x "$REAL_FFMPEG" ]; then
    for p in /usr/lib/jellyfin-ffmpeg/ffmpeg /config/ffmpeg.real /bin/ffmpeg.real /usr/bin/ffmpeg.real /usr/local/bin/ffmpeg.real /bin/ffmpeg /usr/local/bin/ffmpeg /usr/bin/ffmpeg; do
        if [ -x "$p" ]; then
            if [ "$p" != "/bin/ffmpeg" ] || [ -f "/config/ffmpeg.real" ] || [ -f "/bin/ffmpeg.real" ]; then
                if [ -f "/config/ffmpeg.real" ]; then
                    REAL_FFMPEG="/config/ffmpeg.real"
                    break
                elif [ -f "/bin/ffmpeg.real" ]; then
                    REAL_FFMPEG="/bin/ffmpeg.real"
                    break
                else
                    REAL_FFMPEG="$p"
                    break
                fi
            fi
        fi
    done
fi

if [ -z "$REAL_FFMPEG" ] || [ ! -x "$REAL_FFMPEG" ]; then
    if [ -f "/config/ffmpeg.real" ]; then
        REAL_FFMPEG="/config/ffmpeg.real"
    else
        REAL_FFMPEG="/bin/ffmpeg.real"
    fi
fi

INPUT_FILE=""
CROP_VAL=""
HAS_QSV=0
HAS_CUDA=0
HAS_VAAPI=0

# Parse input file and hardware acceleration flags from arguments
PREV_ARG=""
for arg in "$@"; do
    if [ "$PREV_ARG" = "-i" ] && [ -z "$INPUT_FILE" ]; then
        RAW_INPUT="$arg"
        case "$RAW_INPUT" in
            file:*) RAW_INPUT="${RAW_INPUT#file:}" ;;
        esac
        # Strip quotes
        RAW_INPUT=$(echo "$RAW_INPUT" | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")
        INPUT_FILE="$RAW_INPUT"
    fi
    PREV_ARG="$arg"

    case "$arg" in
        *qsv*) HAS_QSV=1 ;;
        *cuda*|*nvenc*) HAS_CUDA=1 ;;
        *vaapi*) HAS_VAAPI=1 ;;
    esac
done

fetch_crop_val() {
    _file="$1"
    _host="$2"
    if command -v curl >/dev/null 2>&1; then
        curl -G -s --connect-timeout 1 -m 2 --data-urlencode "path=$_file" "http://$_host:6795/api/crop_val" 2>/dev/null || true
    elif command -v wget >/dev/null 2>&1; then
        _enc=$(echo "$_file" | sed -e 's/ /%20/g' -e 's/\[/%5B/g' -e 's/\]/%5D/g')
        wget -T 2 -t 1 -qO- "http://$_host:6795/api/crop_val?path=$_enc" 2>/dev/null || true
    fi
}

# If input file was found, query BlackBarr API for crop value
if [ -n "$INPUT_FILE" ]; then
    for host in blackbarr 127.0.0.1 localhost; do
        RES=$(fetch_crop_val "$INPUT_FILE" "$host" || true)
        if [ -n "$RES" ]; then
            CROP_VAL=$(echo "$RES" | grep -o 'crop=[^"]*' | sed 's/crop=//' || true)
            if [ -n "$CROP_VAL" ]; then
                break
            fi
        fi
    done
fi

# If no crop value found in DB, run original ffmpeg command untouched
if [ -z "$CROP_VAL" ]; then
    exec "$REAL_FFMPEG" "$@"
fi

# Build crop filter string
if [ "$CROP_VAL" = "setsar=1" ]; then
    if [ "$HAS_VAAPI" -eq 1 ]; then
        CROP_FILTER="hwdownload,format=nv12,setsar=1,hwupload"
    else
        CROP_FILTER="setsar=1"
    fi
else
    if [ "$HAS_VAAPI" -eq 1 ]; then
        CROP_FILTER="hwdownload,format=nv12,crop=${CROP_VAL},setsar=1,hwupload"
    elif [ "$HAS_QSV" -eq 1 ]; then
        CW=$(echo "$CROP_VAL" | cut -d: -f1)
        CH=$(echo "$CROP_VAL" | cut -d: -f2)
        CX=$(echo "$CROP_VAL" | cut -d: -f3)
        CY=$(echo "$CROP_VAL" | cut -d: -f4)
        if [ -n "$CW" ] && [ -n "$CH" ]; then
            CROP_FILTER="vpp_qsv=crop_w=$CW:crop_h=$CH:crop_x=${CX:-0}:crop_y=${CY:-0},setsar=1"
        else
            CROP_FILTER="crop=${CROP_VAL},setsar=1"
        fi
    else
        CROP_FILTER="crop=${CROP_VAL},setsar=1"
    fi
fi

echo "[BlackBarr Wrapper] Intercepted $INPUT_FILE -> Injecting crop filter: $CROP_FILTER" >&2

FILTER_INJECTED=0
IS_FILTER_ARG=0

# First pass: inject into existing -vf or -filter_complex
for arg in "$@"; do
    if [ "$IS_FILTER_ARG" -eq 1 ]; then
        IS_FILTER_ARG=0
        ORIG_FILTER="$arg"
        
        case "$ORIG_FILTER" in
            \[*:*\]*)
                SPEC=$(echo "$ORIG_FILTER" | grep -o "^\[[0-9a-zA-Z:]*\]")
                REST="${ORIG_FILTER#"$SPEC"}"
                NEW_FILTER="${SPEC}${CROP_FILTER},${REST}"
                set -- "$@" "$NEW_FILTER"
                ;;
            *)
                set -- "$@" "${CROP_FILTER},${ORIG_FILTER}"
                ;;
        esac
        FILTER_INJECTED=1
    else
        case "$arg" in
            -vf|-filter_complex)
                IS_FILTER_ARG=1
                set -- "$@" "$arg"
                ;;
            *)
                set -- "$@" "$arg"
                ;;
        esac
    fi
    shift
done

if [ "$FILTER_INJECTED" -eq 0 ]; then
    INJECTED_VF=0
    TOTAL=$#
    COUNT=0
    while [ "$COUNT" -lt "$TOTAL" ]; do
        arg="$1"
        shift
        COUNT=$((COUNT + 1))
        
        if [ "$INJECTED_VF" -eq 0 ]; then
            case "$arg" in
                -c:v|-codec:v|-vcodec)
                    set -- "$@" "-vf" "$CROP_FILTER"
                    INJECTED_VF=1
                    ;;
            esac
        fi
        set -- "$@" "$arg"
    done

    if [ "$INJECTED_VF" -eq 0 ]; then
        TOTAL=$#
        COUNT=0
        while [ "$COUNT" -lt "$TOTAL" ]; do
            arg="$1"
            shift
            COUNT=$((COUNT + 1))
            if [ "$COUNT" -eq "$TOTAL" ]; then
                set -- "$@" "-vf" "$CROP_FILTER"
            fi
            set -- "$@" "$arg"
        done
    fi
fi

exec "$REAL_FFMPEG" "$@"
