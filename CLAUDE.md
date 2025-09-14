# LoadShaper AI Agent Guidelines

**Self-maintenance:** Keep this file updated, factual, and token-optimized for AI agents.

## ⚠️ Critical: Oracle VM Reclamation Prevention
**VMs reclaimed when ALL metrics <20% for 7 consecutive days:**
- **CPU**: 95th percentile (primary protection)
- **Network**: Simple average (fallback protection)
- **Memory**: Simple average (A1 shapes only)
- **E2 Shapes**: CPU + Network metrics only (50 Mbps cap, 10 Mbps = 20%)
- **A1 Shapes**: CPU + Network + Memory metrics (1 Gbps/vCPU, 0.2 Gbps = 20%)
- **Protection Strategy**: Keep at least one metric >20%, CPU runs at nice 19, network as fallback

## Metrics Storage
- SQLite at `PERSISTENCE_DIR` (default: `/var/lib/loadshaper`), persistent storage REQUIRED
- **Mount Point**: Must be actual mount, not container ephemeral filesystem
- **Instance Lock**: Single instance per storage path via lock file
- CPU: 7-day 95th percentile tracking, 5-second samples, auto-cleanup
- Database corruption recovery via PRAGMA quick_check, WAL mode for concurrency

## Load Protection
- Pauses at load 0.6/core, resumes at 0.4/core (hysteresis)
- **Proportional Scaling**: CPU intensity scales down proportionally with system load
- Workers run at `nice 19` priority, always yielding to legitimate workloads

## P95 CPU Control
- States: BUILDING/MAINTAINING/REDUCING based on 7-day P95
  - **BUILDING**: Ramps up CPU intensity when P95 is below target
  - **MAINTAINING**: Sustains optimal CPU load with controlled exceedances
  - **REDUCING**: Backs off when P95 exceeds target or system load is high
- 60-second slots: 35% (high) or 20% (baseline) intensity
- Target: 22-28% P95 (safe buffer above 20%)
- Ring buffer persistence to `p95_ring_buffer.json` with batched I/O (10 slots/batch)
- **P95 Cache**: 300-second TTL for performance
- **Forced High Slot**: After MAX_CONSECUTIVE_SKIPPED_SLOTS to maintain budget
- **Exceedance Budget Algorithm**:
  ```python
  current_exceedance = high_intensity_slots / total_slots
  target_exceedance = CPU_P95_EXCEEDANCE_TARGET / 100.0
  if current_exceedance < target_exceedance:
      intensity_decision = 1  # High intensity slot
  else:
      intensity_decision = 0  # Normal intensity slot
  ```

## Memory Occupation
- Uses MemAvailable (excludes cache/buffers)
- Occupies memory to maintain target %, not stress testing

## Network Generation
- State machine: OFF→INITIALIZING→VALIDATING→ACTIVE_UDP→ACTIVE_TCP→ERROR
- Fallback chain: UDP→TCP→next peer (NO DNS spam, NO local traffic)
- External peers REQUIRED for VM protection (private traffic doesn't count)
- Peer reputation scoring (0-100), tx_bytes validation
- MTU 9000 optimized: 8900-byte packets by default

## Network Reliability
- **Validation**: tx_bytes verification confirms actual egress, external IP requirement for E2
- **Peer Reputation**: 0-100 scoring, automatic failover on degradation
- **State Transitions**: Debounce/min-on/min-off timers prevent flapping
- **Token Bucket**: 5ms precision rate limiting with burst control

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
Targets: `{CPU,MEM,NET}_{TARGET,STOP}_PCT`
Load: `LOAD_{THRESHOLD,RESUME_THRESHOLD,CHECK_ENABLED}`
Memory: `MEM_{TOUCH_INTERVAL_SEC,STEP_MB,MIN_FREE_MB}`
Network: `NET_{MODE,PEERS,PORT,PROTOCOL,TTL,PACKET_SIZE,IPV6,{MIN,MAX}_RATE_MBIT,{BURST,IDLE}_SEC}`
Fallback: `NET_{ACTIVATION,FALLBACK_{START,STOP}_PCT,FALLBACK_RISK_THRESHOLD_PCT,FALLBACK_{DEBOUNCE,MIN_ON,MIN_OFF,RAMP}_SEC}`
Validation: `NET_{VALIDATE_STARTUP,REQUIRE_EXTERNAL,VALIDATION_TIMEOUT_MS,STATE_{DEBOUNCE,MIN_ON,MIN_OFF}_SEC}`
Sensing: `NET_{SENSE_MODE,IFACE,IFACE_INNER,LINK_MBIT}`
Control: `{CONTROL_PERIOD,AVG_WINDOW,JITTER_PERIOD}_SEC`, `{HYSTERESIS,JITTER}_PCT`
Health: `HEALTH_{ENABLED,PORT,HOST}`
Storage: `PERSISTENCE_DIR`

## System Requirements
- Linux kernel ≥3.14 (MemAvailable support)
- Python 3.8+, stdlib only, no external dependencies
- Container: UID/GID 1000, no root
- K8s: Single replica with RWO PVC

## Shape Detection
- Oracle shapes: Auto-detected via metadata service
- Non-Oracle heuristic: x86_64→E2-like, aarch64→A1-like
- Config precedence: ENV > config-templates/ > defaults
- Detection cache: 5-minute TTL

## Development
- Main code: `loadshaper.py` (single file, multi-process architecture)
- Testing: `python -m pytest -q` in venv, all must pass
- Python 3.8+, stdlib only
- Sync docs: README.md, CONTRIBUTING.md, AGENTS.md, CHANGELOG.md

## Test Coverage
P95: cpu_p95_controller, p95_integration, p95_config_validation
Network: network_state_machine, network_fallback, network_fallback_state, peer_validation, native_network_generator, network_timing, nic_utilization_pct, network_env_integration, network_helper_functions, network_critical_validations
Safety: safety_gating, loadavg, proportional_safety_scaling
Storage: metrics_storage, database_corruption_handling, runtime_failure_handling, ring_buffer_batching
Config: configuration_consistency, oracle_validation, shape_detection, shape_detection_enhanced
Container: entrypoint_validation, mount_verification
Other: health_endpoints, signal_handling, stress_failure_modes, memory_occupation, memory_unpacking

## Documentation Sync
Keep synchronized: README.md, CONTRIBUTING.md, AGENTS.md (technical details), CHANGELOG.md

## Configuration Templates
Oracle shape-specific templates in `config-templates/`:
- `e2-1-micro.env`: E2.1.Micro (x86, 1/8 OCPU) - Conservative CPU, network-focused
- `e2-2-micro.env`: E2.2.Micro (x86, 1/4 OCPU) - Balanced CPU/network
- `a1-flex-1.env`: A1.Flex with 1 vCPU (ARM) - Memory occupation enabled
- `a1-flex-4.env`: A1.Flex with 4 vCPUs (ARM) - Full resource utilization

## Testing Commands
### Performance Verification
```bash
# Resource consumption (expect <1% CPU, 10-20MB memory idle)
docker stats loadshaper --no-stream

# CPU targets verification
docker logs loadshaper | grep -E "cpu now=[0-9.]+" | tail -10

# P95 calculation query
docker exec loadshaper sqlite3 /var/lib/loadshaper/metrics.db \
  "SELECT resource_type, COUNT(*), ROUND(AVG(value), 2) as avg,
   ROUND((SELECT value FROM metrics m2 WHERE m2.resource_type = m1.resource_type
   ORDER BY value DESC LIMIT 1 OFFSET CAST(COUNT(*) * 0.05 as INTEGER)), 2) as p95
   FROM metrics m1 WHERE timestamp > datetime('now', '-7 days')
   GROUP BY resource_type;"

# Integration test workflow
docker compose down -v && docker compose up -d --build
sleep 30 && docker logs loadshaper | tail -10
docker exec loadshaper sqlite3 /var/lib/loadshaper/metrics.db ".tables" || echo "Storage error"
```

## Invariants (Do Not Change)
- 7-day persistence for P95 calculation
- MemAvailable for memory calculation (not MemFree)
- Single instance per PERSISTENCE_DIR (lock file enforced)
- CPU workers at nice 19 priority
- No automatic permission fixes (security by design)
- Mount point verification for persistence

## Breaking Changes
- **Persistent storage MANDATORY** - no fallback to /tmp
- **Rootless security** - UID/GID 1000, no auto-fixes
- **NetworkGenerator rewritten** - state machine, no backwards compatibility
- **NET_PEERS required** - must be external servers you control
- **NET_PACKET_SIZE default** - now 8900 for MTU 9000 (was 1100)
- **Single instance only** - multiple instances corrupt P95/SQLite