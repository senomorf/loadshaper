# LoadShaper AI Agent Guidelines

**Self-maintenance:** Keep this file updated, factual, and token-optimized for AI agents.

## Oracle Free Tier VM Protection
- **Reclamation Rule**: VMs reclaimed when ALL metrics <20% for 7 days (CPU=95th percentile, memory/network=simple average)
- **E2 Shapes**: 50 Mbps cap (10 Mbps threshold), CPU+network only
- **A1.Flex**: 1 Gbps/vCPU (0.2 Gbps threshold), CPU+network+memory
- **Protection Strategy**: Keep at least one metric >20%, CPU runs at nice 19, network as fallback

## Metrics Storage
- SQLite at `PERSISTENCE_DIR` (default: `/var/lib/loadshaper`), persistent storage REQUIRED
- CPU: 7-day 95th percentile tracking, 5-second samples, auto-cleanup
- Database corruption recovery via PRAGMA quick_check

## Load Protection
- Pauses at load 0.6/core, resumes at 0.4/core (hysteresis)
- Workers yield to legitimate workloads

## P95 CPU Control
- States: BUILDING/MAINTAINING/REDUCING based on 7-day P95
- 60-second slots: 35% (high) or 20% (baseline) intensity
- Target: 22-28% P95 (safe buffer above 20%)
- Ring buffer persistence with batched I/O (10 slots/batch)

## Memory Occupation
- Uses MemAvailable (excludes cache/buffers)
- Occupies memory to maintain target %, not stress testing

## Network Generation
- State machine: OFF→INITIALIZING→VALIDATING→ACTIVE_UDP→ACTIVE_TCP→DEGRADED_LOCAL→ERROR
- Fallback chain: UDP→TCP→next peer→DNS servers (8.8.8.8, 1.1.1.1, 9.9.9.9)→local
- Peer reputation scoring (0-100), tx_bytes validation, external address requirement for E2
- DNS queries with EDNS0 padding, rate-limited to 10 QPS

## Rootless Security
- Runs as UID/GID 1000, no root, no auto-permission fixes
- Volume permissions must be set BEFORE deployment:
  ```bash
  # Named volume
  docker run --rm -v loadshaper-metrics:/var/lib/loadshaper alpine chown -R 1000:1000 /var/lib/loadshaper
  # Bind mount
  sudo chown -R 1000:1000 /var/lib/loadshaper
  ```

## Configuration
P95 CPU: `CPU_P95_{TARGET_MIN,TARGET_MAX,SETPOINT,EXCEEDANCE_TARGET,SLOT_DURATION_SEC,HIGH_INTENSITY,BASELINE_INTENSITY,RING_BUFFER_BATCH_SIZE}`
Targets: `{MEM,NET}_TARGET_PCT`, `{CPU,MEM,NET}_STOP_PCT`
Load: `LOAD_{THRESHOLD,RESUME_THRESHOLD,CHECK_ENABLED}`
Memory: `MEM_{TOUCH_INTERVAL_SEC,STEP_MB,MIN_FREE_MB}`
Network: `NET_{MODE,PEERS,PORT,PROTOCOL,TTL,PACKET_SIZE,IPV6,{MIN,MAX}_RATE_MBIT,{BURST,IDLE}_SEC}`
Fallback: `NET_{ACTIVATION,FALLBACK_{START,STOP}_PCT,FALLBACK_RISK_THRESHOLD_PCT,FALLBACK_{DEBOUNCE,MIN_ON,MIN_OFF,RAMP}_SEC}`
Validation: `NET_{VALIDATE_STARTUP,REQUIRE_EXTERNAL,VALIDATION_TIMEOUT_MS,TX_BYTES_MIN_DELTA,DNS_QPS_MAX,STATE_{DEBOUNCE,MIN_ON,MIN_OFF}_SEC}`
Sensing: `NET_{SENSE_MODE,IFACE,IFACE_INNER,LINK_MBIT}`
Control: `{CONTROL_PERIOD,AVG_WINDOW,JITTER_PERIOD}_SEC`, `{HYSTERESIS,JITTER}_PCT`
Health: `HEALTH_{ENABLED,PORT,HOST}`
Storage: `PERSISTENCE_DIR`

## Development
- Testing: `python -m pytest -q` in venv, all must pass
- Python 3.8+, stdlib only, single-process architecture
- Sync docs: README.md, CONTRIBUTING.md, AGENTS.md, CHANGELOG.md

## Test Coverage
P95: cpu_p95_controller, p95_integration, p95_config_validation
Network: network_state_machine, network_fallback*, peer_validation, native_network_generator, network_timing, nic_utilization_pct
Safety: safety_gating, loadavg, proportional_safety_scaling
Storage: metrics_storage, database_corruption_handling, runtime_failure_handling, ring_buffer_batching
Config: configuration_consistency, oracle_validation, shape_detection*
Container: entrypoint_validation, mount_verification
Other: health_endpoints, signal_handling, stress_failure_modes, network_critical_validations

## Documentation Sync
Keep synchronized: README.md, CONTRIBUTING.md, AGENTS.md (technical details), CHANGELOG.md, CLAUDE.md

## Breaking Changes
- **Persistent storage MANDATORY** - no fallback to /tmp
- **Rootless security** - UID/GID 1000, no auto-fixes
- **NetworkGenerator rewritten** - state machine, no backwards compatibility
- **NET_PEERS default** - now 8.8.8.8,1.1.1.1,9.9.9.9 (was RFC2544)
- **Single instance only** - multiple instances corrupt P95/SQLite