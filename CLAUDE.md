# Claude Agent Guidelines

## Core Principles
- **CRITICAL**: Oracle Free Tier VMs are reclaimed when ALL metrics are below 20% for 7 days. Oracle measures CPU using 95th percentile, memory/network using simple thresholds. Keep AT LEAST ONE metric above 20% to prevent reclamation.
- Follow `README.md` for Oracle Free Tier thresholds: E2 shapes cap at 50 Mbps (≈10 Mbps threshold); A1.Flex offers 1 Gbps per vCPU (≈0.2 Gbps threshold) and is the only shape subject to the 20% memory rule.
- CPU stress must run at `nice` 19, use transient bursts, and yield immediately to real workloads.
- Generate network traffic only as a fallback when CPU or memory activity risks dropping below thresholds.
- **Critical**: CPU load must have minimal impact on system responsiveness - always choose the lightest workload type that minimizes latency for other processes.
- **Entrypoint validation**: container exits if persistent storage is missing or not writable.

## 7-Day Metrics & Oracle Compliance
- **Storage**: SQLite database at `/var/lib/loadshaper/metrics.db` (persistent storage required)
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
- **Cold start protection**: Ring buffer state persistence prevents P95 spikes after restarts
- **High-load fallback**: Forces minimum high slots during sustained load to prevent P95 collapse
- **Oracle compliance**: Targets 22-28% P95 range (safe buffer above 20% reclamation threshold)

## Memory Calculation Principles
- **Excludes cache/buffers**: Uses industry-standard calculation that excludes Linux cache/buffers for accurate utilization measurement
- **MemAvailable required**: Uses Linux 3.14+ MemAvailable; container logs an error if unavailable
- **Oracle compliance**: Aligns with cloud provider standards (AWS CloudWatch, Azure Monitor) and Oracle's likely implementation
- **Memory occupation not stressing**: Goal is to maintain target utilization percentage, not stress test memory subsystem

## Key Configuration Variables
- **P95 CPU control**: `CPU_P95_TARGET_MIN`, `CPU_P95_TARGET_MAX`, `CPU_P95_SETPOINT`, `CPU_P95_EXCEEDANCE_TARGET`
- **P95 slot control**: `CPU_P95_SLOT_DURATION_SEC`, `CPU_P95_HIGH_INTENSITY`, `CPU_P95_BASELINE_INTENSITY`
- **Memory/Network targets**: `MEM_TARGET_PCT`, `NET_TARGET_PCT`
- **Safety limits**: `CPU_STOP_PCT`, `MEM_STOP_PCT`, `NET_STOP_PCT`
- **Load monitoring**: `LOAD_THRESHOLD`, `LOAD_RESUME_THRESHOLD`, `LOAD_CHECK_ENABLED`
- **Memory occupation**: `MEM_TOUCH_INTERVAL_SEC`, `MEM_STEP_MB`, `MEM_MIN_FREE_MB`
- **Network detection**: `NET_SENSE_MODE`, `NET_IFACE`, `NET_IFACE_INNER`, `NET_LINK_MBIT`, `NET_PROTOCOL`, `NET_PEERS`
- **Network fallback**: `NET_ACTIVATION`, `NET_FALLBACK_START_PCT`, `NET_FALLBACK_STOP_PCT`, `NET_FALLBACK_DEBOUNCE_SEC`, `NET_FALLBACK_MIN_ON_SEC`, `NET_FALLBACK_MIN_OFF_SEC`, `NET_FALLBACK_RAMP_SEC`
- **Network configuration**: `NET_MODE`, `NET_PORT`, `NET_BURST_SEC`, `NET_IDLE_SEC`, `NET_TTL`, `NET_PACKET_SIZE`, `NET_MIN_RATE_MBIT`, `NET_MAX_RATE_MBIT`
- **Control behavior**: `CONTROL_PERIOD_SEC`, `AVG_WINDOW_SEC`, `HYSTERESIS_PCT`, `JITTER_PCT`, `JITTER_PERIOD_SEC`
- **Health monitoring**: `HEALTH_ENABLED`, `HEALTH_PORT`, `HEALTH_HOST`

## Development Standards
- **Testing**: Always use venv; run `pytest -q` (all tests must pass); install dev dependencies with `pip install -r requirements-dev.txt`
- **Code style**: Python 3.8+, PEP 8, 4-space indentation, minimal dependencies (stdlib only - native network generation)
- **Documentation sync**: Keep `README.md`, `CONTRIBUTING.md`, `AGENTS.md`, `CHANGELOG.md`, and this file synchronized
- **Architecture**: Single-process design with clear component separation (sensors → controller → workers)

## Testing Guidelines
**P95 CPU Control**: Validate state machine and exceedance budget; run `tests/test_cpu_p95_controller.py`, `tests/test_p95_integration.py`
**Memory Management**: Enable `DEBUG_MEM_METRICS=true` and compare excl/incl cache; see `tests/test_memory_occupation.py`
**Load Average Safety**: Verify pause/resume thresholds; see `tests/test_loadavg.py`, `tests/test_safety_gating.py`
**Network Fallback**: Validate activation logic for E2 vs A1; see `tests/test_network_fallback.py`, `tests/test_network_fallback_state.py`
**Persistence**: Confirm `/var/lib/loadshaper/metrics.db` is writable; see `tests/test_metrics_storage.py`, `tests/test_runtime_failure_handling.py`
**Health Endpoints**: Validate `/health` and `/metrics` endpoints; see `tests/test_health_endpoints.py`
**Container Setup**: Test entrypoint validation and permission handling; see `tests/test_entrypoint_validation.py`

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