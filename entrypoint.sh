#!/bin/sh
# LoadShaper Container Entrypoint
# Validates that persistent storage is properly mounted before starting the application

echo "[INFO] LoadShaper container starting..."

# Check persistent storage directory
PERSISTENCE_DIR="/var/lib/loadshaper"

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
# Test actual write capability beyond just -w check
elif ! echo "write_test_$$" > "$PERSISTENCE_DIR/.write_test" 2>/dev/null || ! rm "$PERSISTENCE_DIR/.write_test" 2>/dev/null; then
    USER_ID=$(id -u)
    GROUP_ID=$(id -g)
    echo "[ERROR] Cannot write to $PERSISTENCE_DIR - check volume permissions"
    echo "[ERROR] The container is running as user $USER_ID (group $GROUP_ID)"
    echo "[ERROR] Please ensure the mounted volume is writable by this user"
    echo "[ERROR] LoadShaper requires persistent storage for 7-day P95 calculations"
    echo ""
    echo "Volume permission fix (run on host):"
    echo "  sudo chown -R $USER_ID:$GROUP_ID /path/to/volume/mount"
    echo ""
    echo "Or use Docker Compose with proper user mapping:"
    echo "  services:"
    echo "    loadshaper:"
    echo "      user: \"$USER_ID:$GROUP_ID\""
    echo "      volumes:"
    echo "        - loadshaper-metrics:/var/lib/loadshaper"
    echo ""
    exit 1
else
    echo "[INFO] Persistent storage verified at $PERSISTENCE_DIR"
    echo "[INFO] Running as user $(id -u):$(id -g)"
    echo "[INFO] Metrics database will maintain 7-day P95 history across container restarts"
fi

# Execute the main application
exec "$@"