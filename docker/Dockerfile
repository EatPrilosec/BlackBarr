FROM python:3.11-slim

# Install system dependencies (ffmpeg, sqlite3, curl, bash)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    sqlite3 \
    curl \
    bash \
    va-driver-all \
    mesa-va-drivers \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src /app/src

# Copy scripts
COPY docker/ffmpeg-wrapper.sh /usr/local/bin/ffmpeg-wrapper.sh
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod +x /usr/local/bin/ffmpeg-wrapper.sh /usr/local/bin/entrypoint.sh

# Create persistent storage directories
VOLUME ["/config", "/media"]

ENV BLACKBARR_DB_PATH=/config/BlackBarr.db

EXPOSE 6795 6796 6797

HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=3 CMD curl -f http://localhost:6795/health || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
