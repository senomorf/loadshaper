FROM python:3-alpine

RUN apk add --no-cache procps coreutils curl

# Create non-root user and prepare persistent storage directory
RUN addgroup -g 1000 loadshaper && \
    adduser -D -u 1000 -G loadshaper loadshaper && \
    mkdir -p /var/lib/loadshaper && \
    chown -R loadshaper:loadshaper /var/lib/loadshaper

WORKDIR /app
COPY loadshaper.py /app/

# Add entrypoint script to validate persistent storage
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Health check endpoint (configurable via HEALTH_PORT, default 8080)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:${HEALTH_PORT:-8080}/health || exit 1

# Switch to non-root user for security
USER loadshaper

# Use entrypoint to validate persistent storage before starting application
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-u", "loadshaper.py"]