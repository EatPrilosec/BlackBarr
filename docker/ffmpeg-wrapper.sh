#!/bin/sh
# BlackBarr FFmpeg Wrapper & Dynamic Crop Injector (POSIX /bin/sh Compatible)

# Locate real ffmpeg executable
REAL_FFMPEG="${REAL_FFMPEG_PATH}"

if [ -z "$REAL_FFMPEG" ] || [ ! -x "$REAL_FFMPEG" ]; then
    for p in /usr/lib/jellyfin-ffmpeg/ffmpeg /config/ffmpeg.real /bin/ffmpeg.real /usr/bin/ffmpeg.real /usr/local/bin/ffmpeg.real /bin/ffmpeg /usr/local/bin/ffmpeg /usr/bin/ffmpeg; do
        if [ -x "$p" ]; then
            if [ -f "/config/ffmpeg.real" ]; then
                REAL_FFMPEG="/config/ffmpeg.real"
                break
            elif [ -f "/bin/ffmpeg.real" ]; then
                REAL_FFMPEG="/bin/ffmpeg.real"
                break
            elif [ "$p" != "/bin/ffmpeg" ]; then
                REAL_FFMPEG="$p"
                break
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
        RAW_INPUT=$(echo "$RAW_INPUT" | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")
        INPUT_FILE="$RAW_INPUT"
    fi
    PREV_ARG="$arg"

    case "$arg" in
        *qsv*)           HAS_QSV=1 ;;
        *cuda*|*nvenc*)  HAS_CUDA=1 ;;
        *vaapi*)         HAS_VAAPI=1 ;;
    esac
done

fetch_crop_val() {
    _file="$1"
    _url="$2"
    if command -v curl >/dev/null 2>&1; then
        curl -G -s --connect-timeout 1 -m 2 --data-urlencode "path=$_file" "${_url}/api/crop_val" 2>/dev/null || true
    elif command -v wget >/dev/null 2>&1; then
        _enc=$(echo "$_file" | sed -e 's/ /%20/g' -e 's/\[/%5B/g' -e 's/\]/%5D/g')
        wget -T 2 -t 1 -qO- "${_url}/api/crop_val?path=$_enc" 2>/dev/null || true
    fi
}

# Query BlackBarr API for crop value
if [ -n "$INPUT_FILE" ]; then
    CONFIG_XML="/config/plugins/configurations/Jellyfin.Plugin.BlackBarrHelper.xml"
    API_URL="http://127.0.0.1:6795"

    if [ -f "$CONFIG_XML" ]; then
        EXTRACTED_URL=$(grep -o '<BlackBarrUrl>[^<]*</BlackBarrUrl>' "$CONFIG_XML" | sed -e 's/<BlackBarrUrl>//' -e 's/<\/BlackBarrUrl>//' || true)
        if [ -n "$EXTRACTED_URL" ]; then
            API_URL="$EXTRACTED_URL"
        fi
    fi

    RES=$(fetch_crop_val "$INPUT_FILE" "$API_URL" || true)
    if [ -n "$RES" ]; then
        CROP_VAL=$(echo "$RES" | grep -o 'crop=[^"]*' | sed 's/crop=//' || true)
    fi

    if [ -z "$CROP_VAL" ]; then
        for host in blackbarr 127.0.0.1 localhost; do
            FALLBACK_URL="http://$host:6795"
            if [ "$FALLBACK_URL" != "$API_URL" ]; then
                RES=$(fetch_crop_val "$INPUT_FILE" "$FALLBACK_URL" || true)
                if [ -n "$RES" ]; then
                    CROP_VAL=$(echo "$RES" | grep -o 'crop=[^"]*' | sed 's/crop=//' || true)
                    if [ -n "$CROP_VAL" ]; then break; fi
                fi
            fi
        done
    fi
fi

# Remove and re-inject -max_muxing_queue_size immediately after -i <input>
SKIP_NEXT=0
IS_NEXT_INPUT_VAL=0
INJECTED_QUEUE=0
TOTAL=$#
COUNT=0

while [ "$COUNT" -lt "$TOTAL" ]; do
    arg="$1"; shift
    COUNT=$((COUNT + 1))

    if [ "$SKIP_NEXT" -eq 1 ]; then
        SKIP_NEXT=0
        continue
    fi

    if [ "$arg" = "-max_muxing_queue_size" ]; then
        SKIP_NEXT=1
        continue
    fi

    set -- "$@" "$arg"

    if [ "$IS_NEXT_INPUT_VAL" -eq 1 ]; then
        IS_NEXT_INPUT_VAL=0
        if [ "$INJECTED_QUEUE" -eq 0 ]; then
            set -- "$@" "-max_muxing_queue_size" "10240"
            INJECTED_QUEUE=1
        fi
    elif [ "$arg" = "-i" ]; then
        IS_NEXT_INPUT_VAL=1
    fi
done

if [ "$INJECTED_QUEUE" -eq 0 ]; then
    set -- "$@" "-max_muxing_queue_size" "10240"
fi

# No crop value — pass through unchanged
if [ -z "$CROP_VAL" ]; then
    exec "$REAL_FFMPEG" "$@"
fi

# Parse crop dimensions
CW=$(echo "$CROP_VAL" | cut -d: -f1)
CH=$(echo "$CROP_VAL" | cut -d: -f2)
CX=$(echo "$CROP_VAL" | cut -d: -f3)
CY_RAW=$(echo "$CROP_VAL" | cut -d: -f4)
CY=$(echo "$CY_RAW" | cut -d, -f1)
CX="${CX:-0}"
CY="${CY:-0}"

echo "[BlackBarr Wrapper] Injecting crop=$CROP_VAL into $INPUT_FILE (QSV=$HAS_QSV VAAPI=$HAS_VAAPI CUDA=$HAS_CUDA)" >&2

# ---------------------------------------------------------------------------
# Hardware-aware filter injection
#
# Strategy per hardware path:
#
#   QSV:   vpp_qsv supports crop natively. Merge crop_w/h/x/y into the
#          EXISTING vpp_qsv= token in the filter chain. Never add a second
#          vpp_qsv filter — that is what caused the FFmpeg crash.
#
#   VAAPI: scale_vaapi does NOT support crop. Download to SW, crop, re-upload,
#          then continue with the existing scale_vaapi (or other filters).
#          We insert "hwdownload,format=nv12,crop=W:H:X:Y,setsar=1,hwupload"
#          BEFORE the first scale_vaapi token.
#
#   CUDA:  Similar to VAAPI — hwdownload, SW crop, hwupload_cuda, then the
#          existing scale_cuda or other encoder filters.
#
#   SW:    Prepend "crop=W:H:X:Y,setsar=1" before the existing filter chain.
#
# In all cases we walk the existing -vf value token by token (split on comma)
# and insert at the right place. If no -vf exists at all we add one.
# ---------------------------------------------------------------------------

# build_new_filter <original_filter_value>
#   Outputs the modified filter string to stdout.
build_new_filter() {
    ORIG="$1"
    OUT=""
    DONE=0
    REST="$ORIG"

    while [ -n "$REST" ]; do
        # Split off the first comma-delimited token
        case "$REST" in
            *,*) TOK="${REST%%,*}"; REST="${REST#*,}" ;;
            *)   TOK="$REST";       REST="" ;;
        esac

        if [ "$DONE" -eq 0 ]; then
            if [ "$HAS_QSV" -eq 1 ]; then
                case "$TOK" in
                    vpp_qsv*)
                        # Merge crop into the existing vpp_qsv token using its native cw/ch/cx/cy options.
                        # This crops in hardware on the QSV surface — works for both H264 (nv12)
                        # and HEVC (p010) without needing hwdownload/hwupload.
                        TOK="${TOK}:cw=${CW}:ch=${CH}:cx=${CX}:cy=${CY},setsar=1"
                        DONE=1
                        ;;
                    setparams*)
                        # No vpp_qsv yet — insert a dedicated vpp_qsv crop+format filter
                        TOK="vpp_qsv=cw=${CW}:ch=${CH}:cx=${CX}:cy=${CY}:format=nv12,setsar=1,${TOK}"
                        DONE=1
                        ;;
                esac
            elif [ "$HAS_VAAPI" -eq 1 ]; then
                case "$TOK" in
                    scale_vaapi*|setparams*)
                        # VAAPI has no native crop; download to SW, crop, re-upload
                        TOK="hwdownload,format=nv12,crop=${CW}:${CH}:${CX}:${CY},setsar=1,hwupload,${TOK}"
                        DONE=1
                        ;;
                esac
            elif [ "$HAS_CUDA" -eq 1 ]; then
                case "$TOK" in
                    scale_cuda*)
                        TOK="hwdownload,format=nv12,crop=${CW}:${CH}:${CX}:${CY},setsar=1,hwupload_cuda,${TOK}"
                        DONE=1
                        ;;
                    setparams*)
                        TOK="hwdownload,format=nv12,crop=${CW}:${CH}:${CX}:${CY},setsar=1,hwupload_cuda,${TOK}"
                        DONE=1
                        ;;
                esac
            else
                # Software — prepend crop before the very first token
                TOK="crop=${CW}:${CH}:${CX}:${CY},setsar=1,${TOK}"
                DONE=1
            fi
        fi

        if [ -z "$OUT" ]; then
            OUT="$TOK"
        else
            OUT="${OUT},${TOK}"
        fi
    done

    # No recognised insertion point found — prepend SW crop as last resort
    if [ "$DONE" -eq 0 ]; then
        OUT="crop=${CW}:${CH}:${CX}:${CY},setsar=1,${OUT}"
    fi

    echo "$OUT"
}

# Walk all arguments looking for -vf / -filter_complex and rewrite them
FILTER_INJECTED=0
IS_FILTER_ARG=0
FILTER_FLAG=""

for arg in "$@"; do
    if [ "$IS_FILTER_ARG" -eq 1 ]; then
        IS_FILTER_ARG=0
        NEW_VAL=$(build_new_filter "$arg")
        set -- "$@" "$NEW_VAL"
        FILTER_INJECTED=1
    else
        case "$arg" in
            -vf|-filter_complex)
                IS_FILTER_ARG=1
                FILTER_FLAG="$arg"
                set -- "$@" "$arg"
                ;;
            *)
                set -- "$@" "$arg"
                ;;
        esac
    fi
    shift
done

# If no -vf was present, insert one before the video codec flag
if [ "$FILTER_INJECTED" -eq 0 ]; then
    SW_CROP="crop=${CW}:${CH}:${CX}:${CY},setsar=1"
    if [ "$HAS_QSV" -eq 1 ]; then
        HW_CROP="vpp_qsv=cw=${CW}:ch=${CH}:cx=${CX}:cy=${CY}:format=nv12,setsar=1"
    elif [ "$HAS_VAAPI" -eq 1 ]; then
        HW_CROP="hwdownload,format=nv12,crop=${CW}:${CH}:${CX}:${CY},setsar=1,hwupload"
    elif [ "$HAS_CUDA" -eq 1 ]; then
        HW_CROP="hwdownload,format=nv12,crop=${CW}:${CH}:${CX}:${CY},setsar=1,hwupload_cuda"
    else
        HW_CROP="$SW_CROP"
    fi

    INJECTED_VF=0
    TOTAL=$#
    COUNT=0
    while [ "$COUNT" -lt "$TOTAL" ]; do
        arg="$1"; shift
        COUNT=$((COUNT + 1))
        if [ "$INJECTED_VF" -eq 0 ]; then
            case "$arg" in
                -c:v|-codec:v|-vcodec)
                    set -- "$@" "-vf" "$HW_CROP"
                    INJECTED_VF=1
                    ;;
            esac
        fi
        set -- "$@" "$arg"
    done

    if [ "$INJECTED_VF" -eq 0 ]; then
        set -- "$@" "-vf" "$HW_CROP"
    fi
fi

exec "$REAL_FFMPEG" "$@"
