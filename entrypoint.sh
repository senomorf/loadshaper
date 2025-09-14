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
# Check if the persistence directory is actually a mount point
# This prevents using the image's built-in directory which would lose data on restart
# Use Python for portable device number detection across Alpine/busybox versions
elif [ -d "$PERSISTENCE_DIR" ]; then
    # Get device numbers using portable Python approach
    DEV_PERSISTENT=$(python3 -c "import os; print(os.stat('$PERSISTENCE_DIR').st_dev)" 2>/dev/null)
    DEV_PARENT=$(python3 -c "import os; print(os.stat('$PERSISTENCE_DIR/..').st_dev)" 2>/dev/null)

    # Verify device detection succeeded
    if [ -z "$DEV_PERSISTENT" ] || [ -z "$DEV_PARENT" ]; then
        echo "[ERROR] Failed to detect mount point status for $PERSISTENCE_DIR"
        echo "[ERROR] Ensure Python3 is available and directory is accessible"
        exit 1
    fi

    # Check if same device (not a mount point)
    if [ "$DEV_PERSISTENT" = "$DEV_PARENT" ]; then
        echo "[ERROR] $PERSISTENCE_DIR exists but is NOT a mount point"
        echo "[ERROR] LoadShaper requires a persistent volume to be mounted at this path"
        echo "[ERROR] Data stored in the container's filesystem will be lost on restart"
        echo ""
        echo "This typically means:"
        echo "  - No volume is mounted to the container"
        echo "  - The directory exists in the Docker image (should not happen)"
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
        echo "To verify mount status inside container:"
        echo "  docker exec loadshaper mount | grep $PERSISTENCE_DIR"
        echo ""
        exit 1
    fi
# Test actual write capability using mktemp for better security
elif ! TMPFILE=$(mktemp "$PERSISTENCE_DIR/.write_test.XXXXXX" 2>/dev/null); then
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
    # Clean up the temp file immediately after successful creation
    rm -f "$TMPFILE" 2>/dev/null || true
    echo "[INFO] Persistent storage verified at $PERSISTENCE_DIR (mount point confirmed)"
    echo "[INFO] Running as user $(id -u):$(id -g)"
    echo "[INFO] Metrics database will maintain 7-day P95 history across container restarts"
fi

# Execute the main application
exec "$@"