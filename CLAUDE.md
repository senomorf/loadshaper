# Claude Agent Guidelines

## Core Principles
- **CRITICAL**: Oracle Free Tier VMs are reclaimed when ALL metrics are below 20% for 7 days. Oracle measures CPU using 95th percentile, memory/network using simple thresholds. Keep AT LEAST ONE metric above 20% to prevent reclamation.
- Follow `README.md` for Oracle Free Tier thresholds: E2 shapes cap at 50 Mbps (≈10 Mbps threshold); A1.Flex offers 1 Gbps per vCPU (≈0.2 Gbps threshold) and is the only shape subject to the 20% memory rule.
- CPU stress must run at `nice` 19, use transient bursts, and yield immediately to real workloads.
- Generate network traffic only as a fallback when CPU or memory activity risks dropping below thresholds.
- **Critical**: CPU load must have minimal impact on system responsiveness - always choose the lightest workload type that minimizes latency for other processes.
- **Entrypoint validation**: container exits if persistent storage is missing or not writable - no automatic fixes provided (rootless security)
- **Rootless container philosophy**: Never runs as root, no privilege escalation, user must configure volume permissions

## 7-Day Metrics & Oracle Compliance
- **Storage**: SQLite database at configurable path via `PERSISTENCE_DIR` env var (default: `/var/lib/loadshaper/metrics.db`) - persistent storage required
- **95th percentile tracking**: CPU only (mirrors Oracle's measurement method for CPU; memory/network use simple averages)
- **Sample frequency**: Every 5 seconds (≈120,960 samples per week, 10-20MB database size)
- **Automatic cleanup**: Removes data older than 7 days, requires persistent storage for 7-day P95 calculations
- **Oracle official rules**: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm#compute__idleinstances

## Load Average Monitoring
- **Default thresholds**: `LOAD_THRESHOLD=0.6` (pause), `LOAD_RESUME_THRESHOLD=0.4` (resume)
- **Hysteresis gap**: Prevents oscillation when load hovers near thresholds
- **Automatic pausing**: Workers pause when system load indicates CPU contention from legitimate processes
- **Per-core calculation**: Thresholds applied per CPU core for accurate scaling

## P95 CPU Control Implementation
- **State machine**: BUILDING/MAINTAINING/REDUCING states based on 7-day P95 trends
- **Exceedance budget**: Maintains ~6.5% of time slots at high intensity to achieve target P95
- **Slot-based control**: 60-second slots with high (35%) or baseline (20%) CPU intensity
- **Adaptive hysteresis**: Prevents oscillation with state-dependent deadbands
- **Safety scaling**: Proportional intensity reduction based on system load
- **Cold start protection**: Ring buffer state persistence prevents P95 spikes after restarts with batched I/O for performance
- **High-load fallback**: Forces minimum high slots during sustained load to prevent P95 collapse
- **Oracle compliance**: Targets 22-28% P95 range (safe buffer above 20% reclamation threshold)
- **Database corruption recovery**: Automatic detection via PRAGMA quick_check with backup and recovery

## Memory Calculation Principles
- **Excludes cache/buffers**: Uses industry-standard calculation that excludes Linux cache/buffers for accurate utilization measurement
- **MemAvailable required**: Uses Linux 3.14+ MemAvailable; container logs an error if unavailable
- **Oracle compliance**: Aligns with cloud provider standards (AWS CloudWatch, Azure Monitor) and Oracle's likely implementation
- **Memory occupation not stressing**: Goal is to maintain target utilization percentage, not stress test memory subsystem

## Network Generation Reliability (Issue #75 Implementation)
- **State machine architecture**: OFF → INITIALIZING → VALIDATING → ACTIVE_UDP → ACTIVE_TCP → DEGRADED_LOCAL → ERROR
- **External address validation**: Automatically rejects RFC1918, loopback, and link-local addresses for E2 Oracle compliance
- **tx_bytes monitoring**: Runtime validation of actual network traffic via `/sys/class/net/*/statistics/tx_bytes`
- **Peer reputation system**: EMA-based scoring (0-100) tracks reliability over time
- **Automatic fallback chain**: UDP → TCP → next peer → DNS servers (8.8.8.8, 1.1.1.1, 9.9.9.9) → local generation
- **DNS packet generation**: EDNS0-padded queries for reliable external traffic when peers fail
- **Network health scoring**: Weighted composite score (state 40%, reputation 30%, validation 20%, errors 10%)
- **Default peers changed**: From RFC2544 placeholders (10.0.0.2, 10.0.0.3) to public DNS servers for reliability
- **Oracle E2 compliance**: Ensures external traffic generation required for 20% network threshold

## Rootless Container Security

LoadShaper follows **strict rootless container principles**:

- **Never runs as root**: Container always executes as user `loadshaper` (UID/GID 1000)
- **No privilege escalation**: No automatic permission fixing or root operations in entrypoint
- **User responsibility**: Volume permissions must be configured correctly BEFORE deployment
- **Security first**: Prevents container breakout and follows container security best practices
- **Environment variable support**: `PERSISTENCE_DIR` configures storage path (both entrypoint and loadshaper.py)

### Required Volume Permission Setup
```bash
# For Docker named volumes (most common)
docker run --rm -v loadshaper-metrics:/var/lib/loadshaper alpine:latest chown -R 1000:1000 /var/lib/loadshaper

# For bind mounts
sudo mkdir -p /var/lib/loadshaper
sudo chown -R 1000:1000 /var/lib/loadshaper
sudo chmod -R 755 /var/lib/loadshaper
```

### Why Rootless?
- Eliminates container security vulnerabilities
- Follows least-privilege principle
- Compatible with security-conscious environments (Kubernetes, OpenShift)
- Prevents accidental host system modifications

## Key Configuration Variables
- **P95 CPU control**: `CPU_P95_TARGET_MIN`, `CPU_P95_TARGET_MAX`, `CPU_P95_SETPOINT`, `CPU_P95_EXCEEDANCE_TARGET`
- **P95 slot control**: `CPU_P95_SLOT_DURATION_SEC`, `CPU_P95_HIGH_INTENSITY`, `CPU_P95_BASELINE_INTENSITY`
- **P95 performance**: `CPU_P95_RING_BUFFER_BATCH_SIZE` - saves ring buffer state every N slots to reduce I/O (default: 10)
- **Memory/Network targets**: `MEM_TARGET_PCT`, `NET_TARGET_PCT`
- **Safety limits**: `CPU_STOP_PCT`, `MEM_STOP_PCT`, `NET_STOP_PCT`
- **Load monitoring**: `LOAD_THRESHOLD`, `LOAD_RESUME_THRESHOLD`, `LOAD_CHECK_ENABLED`
- **Memory occupation**: `MEM_TOUCH_INTERVAL_SEC`, `MEM_STEP_MB`, `MEM_MIN_FREE_MB`
- **Network detection**: `NET_SENSE_MODE`, `NET_IFACE`, `NET_IFACE_INNER`, `NET_LINK_MBIT`, `NET_PROTOCOL`, `NET_PEERS`
- **Network fallback**: `NET_ACTIVATION`, `NET_FALLBACK_START_PCT`, `NET_FALLBACK_STOP_PCT`, `NET_FALLBACK_DEBOUNCE_SEC`, `NET_FALLBACK_MIN_ON_SEC`, `NET_FALLBACK_MIN_OFF_SEC`, `NET_FALLBACK_RAMP_SEC`
- **Network configuration**: `NET_MODE`, `NET_PORT`, `NET_BURST_SEC`, `NET_IDLE_SEC`, `NET_TTL`, `NET_PACKET_SIZE`, `NET_MIN_RATE_MBIT`, `NET_MAX_RATE_MBIT`
- **Control behavior**: `CONTROL_PERIOD_SEC`, `AVG_WINDOW_SEC`, `HYSTERESIS_PCT`, `JITTER_PCT`, `JITTER_PERIOD_SEC`
- **Health monitoring**: `HEALTH_ENABLED`, `HEALTH_PORT`, `HEALTH_HOST`
- **Storage configuration**: `PERSISTENCE_DIR` - configures persistent storage directory path (default: `/var/lib/loadshaper`)

## Development Standards
- **Testing**: Always use venv; run `pytest -q` (all tests must pass); install dev dependencies with `pip install -r requirements-dev.txt`
- **Test Requirements**: Tests need proper global variable initialization; use `python -m pytest` instead of direct execution
- **Code style**: Python 3.8+, PEP 8, 4-space indentation, minimal dependencies (stdlib only - native network generation)
- **Documentation sync**: Keep `README.md`, `CONTRIBUTING.md`, `AGENTS.md`, `CHANGELOG.md`, and this file synchronized
- **Architecture**: Single-process design with clear component separation (sensors → controller → workers)

## Testing Guidelines
**P95 CPU Control**: Validate state machine and exceedance budget; run `tests/test_cpu_p95_controller.py`, `tests/test_p95_integration.py`
**Ring Buffer Batching**: Test I/O optimization with different batch sizes; see `tests/test_ring_buffer_batching.py`
**Thread Safety**: Validate race condition prevention in ring buffer saves with PID+thread temp files; see `tests/test_runtime_failure_handling.py`
**ENOSPC Degraded Mode**: Test comprehensive disk full scenarios and graceful degradation; see `tests/test_runtime_failure_handling.py` (3 new test methods)
**Configuration Validation**: Test cross-parameter consistency checks; see `tests/test_configuration_consistency.py`
**Database Corruption**: Test detection and recovery mechanisms; see `tests/test_database_corruption_handling.py`
**Memory Management**: Enable `DEBUG_MEM_METRICS=true` and compare excl/incl cache; see `tests/test_memory_occupation.py`
**Load Average Safety**: Verify pause/resume thresholds; see `tests/test_loadavg.py`, `tests/test_safety_gating.py`
**Network Fallback**: Validate activation logic for E2 vs A1; see `tests/test_network_fallback.py`, `tests/test_network_fallback_state.py`
**Persistence**: Confirm `/var/lib/loadshaper/metrics.db` is writable; see `tests/test_metrics_storage.py`, `tests/test_runtime_failure_handling.py`
**Health Endpoints**: Validate `/health` and `/metrics` endpoints; see `tests/test_health_endpoints.py`
**Container Setup**: Test entrypoint validation and permission handling; see `tests/test_entrypoint_validation.py`
**Portable Mount Detection**: Verify Python-based device detection works across Alpine/busybox; see entrypoint validation tests

## Documentation Synchronization Requirement
**CRITICAL**: When implementing changes or adjusting documentation, you MUST update all relevant files:
1. **Code changes** → Update `README.md` configuration tables and `AGENTS.md` technical details
2. **New features** → Add to `CHANGELOG.md` and update this file's guidelines
3. **Configuration changes** → Synchronize across `README.md`, `CONTRIBUTING.md`, and this file
4. **Testing changes** → Update `CONTRIBUTING.md` and `AGENTS.md` testing sections
5. **Always verify consistency** between all documentation files before committing

**Files to keep in sync**: `README.md`, `CONTRIBUTING.md`, `AGENTS.md`, `CHANGELOG.md`, `CLAUDE.md`

## Project Development Status
**This is a Work In Progress project.** Breaking changes are intentionally introduced without migration paths as we iterate toward the optimal Oracle Cloud VM protection solution. This approach allows rapid innovation and prevents technical debt accumulation during active development phases.

### Current Breaking Changes: Network Generation + Rootless Security
**CRITICAL CHANGES:**
1. **Persistent storage is now MANDATORY** - All fallback to `/tmp` storage has been completely removed by design
2. **Rootless container security enforced** - No automatic permission fixing, user must configure volumes
3. **NetworkGenerator completely rewritten** - New state machine architecture with no backwards compatibility
4. **Default NET_PEERS changed** - From RFC2544 placeholders (10.0.0.2, 10.0.0.3) to public DNS servers (8.8.8.8, 1.1.1.1, 9.9.9.9)

**Implementation details:**
- **Container startup**: Will fail immediately if persistent storage directory (configurable via `PERSISTENCE_DIR`) is not writable
- **No migration path**: Existing deployments without persistent volumes must be updated
- **No automatic fixes**: Container will NOT attempt to fix volume permissions - user responsibility
- **Oracle compliance**: 7-day P95 CPU history requires persistent metrics database
- **Security first**: Container runs as UID/GID 1000 with no privilege escalation
- **Network reliability**: State machine ensures continuous traffic generation with automatic fallback
- **Multi-instance protection**: Race condition warnings enhanced to prevent P95 calculation corruption

### Single Instance Requirement
**CRITICAL:** Only ONE LoadShaper instance per system. Multiple instances cause:
- P95 ring buffer state corruption (`/var/lib/loadshaper/p95_ring_buffer.json`)
- SQLite database locks and corruption (`/var/lib/loadshaper/metrics.db`)
- Oracle VM reclamation due to broken P95 calculations
- Resource measurement conflicts and safety check failures

Each instance requires **exclusive access** to `/var/lib/loadshaper/` directory.