#!/bin/sh
# LoadShaper Container Entrypoint
# Validates that persistent storage is properly mounted before starting the application

echo "[INFO] LoadShaper container starting..."

# Check if persistent storage directory is writable
if [ -w /var/lib/loadshaper ]; then
    echo "[INFO] Persistent storage verified at /var/lib/loadshaper"
    echo "[INFO] Metrics database will maintain 7-day P95 history across container restarts"
else
    echo "[ERROR] Cannot write to /var/lib/loadshaper - persistent volume not properly mounted"
    echo "[ERROR] LoadShaper requires persistent storage to maintain 7-day P95 CPU history"
    echo "[ERROR] Without persistent metrics, Oracle VM reclamation detection will not work correctly"
    echo ""
    echo "Required Docker Compose configuration:"
    echo "  services:"
    echo "    loadshaper:"
    echo "      volumes:"
    echo "        - loadshaper-metrics:/var/lib/loadshaper"
    echo ""
    echo "  volumes:"
    echo "    loadshaper-metrics:"
    echo "      driver: local"
    echo ""
    exit 1
fi

# Execute the main application
exec "$@"