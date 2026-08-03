# Stage 1: Build Jellyfin C# Plugin (Jellyfin 10.11.11 / .NET 9.0)
FROM mcr.microsoft.com/dotnet/sdk:9.0 AS plugin-builder
WORKDIR /src
COPY src/plugin/jellyfin /src
RUN dotnet publish -c Release -o /out && \
    rm -rf /out/MediaBrowser* /out/Microsoft* /out/System* /out/ICU4N* /out/J2N* /out/Polly* /out/NEbml* /out/BitFaster* /out/Diacritics* /out/Emby.Naming* /out/Jellyfin.Data* /out/Jellyfin.Database* /out/Jellyfin.Extensions* /out/Jellyfin.MediaEncoding* && \
    cp /src/meta.json /out/ 2>/dev/null || true

# Stage 2: Final BlackBarr App Image
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

# Copy compiled C# plugin DLL
COPY --from=plugin-builder /out /app/plugins/jellyfin

# Copy scripts
COPY docker/ffmpeg-wrapper.sh /usr/local/bin/ffmpeg-wrapper.sh
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod +x /usr/local/bin/ffmpeg-wrapper.sh /usr/local/bin/entrypoint.sh

# Create persistent storage directories
VOLUME ["/config", "/media"]

ENV BLACKBARR_DB_PATH=/config/BlackBarr.db

EXPOSE 6795

HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=3 CMD curl -f http://localhost:6795/health || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
