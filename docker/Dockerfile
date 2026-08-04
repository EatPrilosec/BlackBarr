# Stage 1: Build Jellyfin & Emby C# Plugins (.NET 9.0)
FROM mcr.microsoft.com/dotnet/sdk:9.0 AS plugin-builder
WORKDIR /src

# Build Jellyfin Plugin
COPY src/plugin/jellyfin /src/jellyfin
RUN cd /src/jellyfin && dotnet publish -c Release -o /out/jellyfin && \
    rm -rf /out/jellyfin/MediaBrowser* /out/jellyfin/Microsoft* /out/jellyfin/System* /out/jellyfin/ICU4N* /out/jellyfin/J2N* /out/jellyfin/Polly* /out/jellyfin/NEbml* /out/jellyfin/BitFaster* /out/jellyfin/Diacritics* /out/jellyfin/Emby.Naming* /out/jellyfin/Jellyfin.Data* /out/jellyfin/Jellyfin.Database* /out/jellyfin/Jellyfin.Extensions* /out/jellyfin/Jellyfin.MediaEncoding*

# Build Emby Plugin
COPY src/plugin/emby /src/emby
RUN cd /src/emby && dotnet publish -c Release -o /out/emby && \
    rm -rf /out/emby/MediaBrowser* /out/emby/Microsoft* /out/emby/System*

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

# Copy compiled C# plugin DLLs
COPY --from=plugin-builder /out/jellyfin /app/plugins/jellyfin
COPY --from=plugin-builder /out/emby /app/plugins/emby

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
