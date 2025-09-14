#!/bin/sh
set -eu
# LoadShaper Container Entrypoint
# Validates that persistent storage is properly mounted before starting the application

echo "[INFO] LoadShaper container starting..."

# Check persistent storage directory
# Allow override for testing, default to production path
PERSISTENCE_DIR="${PERSISTENCE_DIR:-/var/lib/loadshaper}"

if [ ! -d "$PERSISTENCE_DIR" ]; then
    echo "[ERROR] Persistent storage directory does not exist: $PERSISTENCE_DIR"
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
# Test actual write capability beyond just -w check (with secure temp file creation)
elif ! (umask 077; echo "write_test_$$" > "$PERSISTENCE_DIR/.write_test.$$") 2>/dev/null || ! rm "$PERSISTENCE_DIR/.write_test.$$" 2>/dev/null; then
    USER_ID=$(id -u)
    GROUP_ID=$(id -g)
    echo "[ERROR] Cannot write to $PERSISTENCE_DIR - check volume permissions"
    echo "[ERROR] LoadShaper follows rootless container philosophy and runs as user $USER_ID (group $GROUP_ID)"
    echo "[ERROR] Volume permissions must be configured correctly before starting the container"
    echo "[ERROR] LoadShaper requires persistent storage for 7-day P95 calculations"
    echo ""
    echo "REQUIRED: Fix volume permissions before starting LoadShaper"
    echo ""
    echo "For Docker named volumes (recommended):"
    echo "  docker run --rm -v loadshaper-metrics:/var/lib/loadshaper alpine:latest chown -R 1000:1000 /var/lib/loadshaper"
    echo ""
    echo "For bind mounts:"
    echo "  sudo chown -R 1000:1000 /path/to/host/directory"
    echo "  sudo chmod -R 755 /path/to/host/directory"
    echo ""
    echo "For Kubernetes/Podman with different UID:"
    echo "  Adjust user: field in your deployment to match volume ownership"
    echo ""
    echo "See README.md for detailed rootless setup instructions"
    echo ""
    exit 1
else
    echo "[INFO] Persistent storage verified at $PERSISTENCE_DIR"
    echo "[INFO] Running as user $(id -u):$(id -g)"
    echo "[INFO] Metrics database will maintain 7-day P95 history across container restarts"
fi

# Execute the main application
exec "$@"