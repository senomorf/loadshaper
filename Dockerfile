FROM python:3-alpine

RUN apk add --no-cache iperf3 procps coreutils curl

WORKDIR /app
COPY loadshaper.py /app/

# Health check endpoint (default port 8080)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["python", "-u", "loadshaper.py"]