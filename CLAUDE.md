# Claude Agent Guidelines

## Core Principles
- **CRITICAL**: Oracle Free Tier VMs are reclaimed when ALL metrics are below 20% for 7 days (95th percentile). Keep AT LEAST ONE metric above 20% to prevent reclamation.
- Follow `README.md` for Oracle Free Tier thresholds: E2 shapes cap at 50 Mbps (≈10 Mbps threshold); A1.Flex offers 1 Gbps per vCPU (≈0.2 Gbps threshold) and is the only shape subject to the 20% memory rule.
- CPU stress must run at `nice` 19, use transient bursts, and yield immediately to real workloads.
- Generate network traffic only as a fallback when CPU or memory activity risks dropping below thresholds.
- **Critical**: CPU load must have minimal impact on system responsiveness - always choose the lightest workload type that minimizes latency for other processes.

## 7-Day Metrics & Oracle Compliance
- **Storage**: SQLite database at `/var/lib/loadshaper/metrics.db` (primary) or `/tmp/loadshaper_metrics.db` (fallback)
- **95th percentile tracking**: Mirrors Oracle's exact reclamation criteria over 7-day rolling windows
- **Sample frequency**: Every 5 seconds (≈120,960 samples per week, 10-20MB database size)
- **Automatic cleanup**: Removes data older than 7 days, handles storage failures gracefully

## Load Average Monitoring
- **Default thresholds**: `LOAD_THRESHOLD=0.6` (pause), `LOAD_RESUME_THRESHOLD=0.4` (resume)
- **Hysteresis gap**: Prevents oscillation when load hovers near thresholds
- **Automatic pausing**: Workers pause when system load indicates CPU contention from legitimate processes
- **Per-core calculation**: Thresholds applied per CPU core for accurate scaling

## Memory Calculation Principles
- **Excludes cache/buffers**: Uses industry-standard calculation that excludes Linux cache/buffers for accurate utilization measurement
- **MemAvailable preferred**: Uses Linux 3.14+ MemAvailable when available, falls back to manual calculation for older kernels
- **Oracle compliance**: Aligns with cloud provider standards (AWS CloudWatch, Azure Monitor) and Oracle's likely implementation
- **Memory occupation not stressing**: Goal is to maintain target utilization percentage, not stress test memory subsystem
- **Debug metrics**: Set `DEBUG_MEM_METRICS=true` to compare both calculation methods in telemetry

## Key Configuration Variables
- **Core targets**: `CPU_TARGET_PCT`, `MEM_TARGET_PCT`, `NET_TARGET_PCT`
- **Safety limits**: `CPU_STOP_PCT`, `MEM_STOP_PCT`, `NET_STOP_PCT`
- **Load monitoring**: `LOAD_THRESHOLD`, `LOAD_RESUME_THRESHOLD`, `LOAD_CHECK_ENABLED`
- **Memory occupation**: `MEM_TOUCH_INTERVAL_SEC`, `MEM_STEP_MB`, `MEM_MIN_FREE_MB`
- **Network detection**: `NET_SENSE_MODE`, `NET_LINK_MBIT`, `NET_PROTOCOL`, `NET_PEERS`
- **Control behavior**: `CONTROL_PERIOD_SEC`, `AVG_WINDOW_SEC`, `HYSTERESIS_PCT`

## Development Standards
- **Testing**: Run `python -m pytest -q` (all tests must pass)
- **Code style**: Python 3.8+, PEP 8, 4-space indentation, minimal dependencies (stdlib + `iperf3`)
- **Documentation sync**: Keep `README.md`, `CONTRIBUTING.md`, `AGENTS.md`, `CHANGELOG.md`, and this file synchronized
- **Architecture**: Single-process design with clear component separation (sensors → controller → workers)

## Documentation Synchronization Requirement
**CRITICAL**: When implementing changes or adjusting documentation, you MUST update all relevant files:
1. **Code changes** → Update `README.md` configuration tables and `AGENTS.md` technical details
2. **New features** → Add to `CHANGELOG.md` and update this file's guidelines
3. **Configuration changes** → Synchronize across `README.md`, `CONTRIBUTING.md`, and this file
4. **Testing changes** → Update `CONTRIBUTING.md` and `AGENTS.md` testing sections
5. **Always verify consistency** between all documentation files before committing

**Files to keep in sync**: `README.md`, `CONTRIBUTING.md`, `AGENTS.md`, `CHANGELOG.md`, `CLAUDE.md`