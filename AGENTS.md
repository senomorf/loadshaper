# Repository Guidelines

**⚠️ WIP Project:** Implementation details are subject to change without notice. Breaking changes are introduced intentionally to prevent technical debt during active development.

**📖 Related Documentation:**
- [README.md](README.md) - Project overview, usage, and configuration
- [CONTRIBUTING.md](CONTRIBUTING.md) - Contributor setup and guidelines
- [CHANGELOG.md](CHANGELOG.md) - Version history and breaking changes

## Architecture Overview

`loadshaper` is designed as a single-service monitoring and load generation system with internal worker processes and clear separation of concerns:

### Core Components
- **Metric Collection**: System-level monitoring (CPU, memory, network, load average)
- **Storage Layer**: SQLite-based 7-day rolling metrics with CPU 95th percentile calculations
- **Control Logic**: P95 CPU controller with state machine, memory nurse thread, and network fallback with hysteresis
- **Load Workers**: Low-priority background processes for resource consumption
- **Safety Systems**: Load average monitoring and automatic yielding to real workloads

### Component Interactions
```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   Sensors   │───▶│ Controller  │───▶│  Workers    │
│ (CPU/MEM/   │    │  (PID +     │    │ (CPU/MEM/   │
│  NET/LOAD)  │    │ Hysteresis) │    │  Network)   │
└─────────────┘    └─────────────┘    └─────────────┘
       │                   │                   │
       ▼                   ▼                   ▼
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   SQLite    │    │   Safety    │    │   Telemetry │
│  Metrics    │    │  Monitors   │    │   Output    │
│ (CPU 7d p95)│    │ (Load Avg)  │    │ (Logs/UI)   │
└─────────────┘    └─────────────┘    └─────────────┘
```

### Security Architecture

LoadShaper implements **strict rootless container security** throughout:

#### Container Security Model
- **Never runs as root**: Container executes as user `loadshaper` (UID/GID 1000)
- **No privilege escalation**: Entrypoint validation fails immediately without attempting automatic fixes
- **Volume permissions**: User responsibility to configure persistent storage permissions BEFORE deployment
- **Least-privilege principle**: Minimal attack surface with no unnecessary capabilities

#### Storage Security
- **Configurable paths**: `PERSISTENCE_DIR` environment variable supports custom storage locations
- **Permission validation**: Pre-flight checks ensure write access without granting it
- **No fallback paths**: Eliminates `/tmp` fallback that could mask permission issues
- **Fail-fast validation**: Container exits immediately if storage requirements not met

#### Why Rootless?
- **Container breakout prevention**: Non-root execution prevents privilege escalation attacks
- **Host protection**: Cannot modify host system files or permissions
- **Compliance ready**: Compatible with security-conscious environments (OpenShift, hardened Kubernetes)
- **Audit friendly**: Clear security posture with no ambiguous privilege usage

#### Required Setup
```bash
# Docker named volumes (recommended)
docker run --rm -v loadshaper-metrics:/var/lib/loadshaper alpine:latest chown -R 1000:1000 /var/lib/loadshaper

# Kubernetes security context
securityContext:
  runAsUser: 1000
  runAsGroup: 1000
  fsGroup: 1000
```

### Design Principles
- **Unobtrusive**: Always yields to legitimate workloads (nice 19 priority)
- **Adaptive**: Responds to system load and Oracle's reclamation criteria
- **Resilient**: Handles storage failures, network issues, database corruption, and system restarts
- **Observable**: Rich telemetry for monitoring and debugging
- **Self-validating**: Comprehensive configuration validation prevents operational issues
- **Security-first**: Rootless container execution with no privilege escalation or automatic permission fixes

### P95 CPU Controller Technical Details

The **CPUP95Controller** implements advanced 95th percentile targeting using a state machine approach:

#### State Machine Architecture
- **BUILDING**: Ramps up CPU intensity when P95 is below target
- **MAINTAINING**: Sustains optimal CPU load with controlled exceedances
- **REDUCING**: Backs off when P95 exceeds target or system load is high

#### Exceedance Budget Management
- **Slot-based tracking**: 24-hour ring buffer of configurable slot-based decisions (default 60s)
- **Exceedance ratio**: `CPU_P95_EXCEEDANCE_TARGET` (default 6.5%) controls high-intensity slots
- **Adaptive scaling**: Load-based intensity reduction maintains system responsiveness
- **Cache optimization**: 300-second TTL for P95 calculations reduces database load

#### Technical Implementation
```python
# Core algorithm: Maintain exceedance budget while achieving P95 target
current_exceedance = high_intensity_slots / total_slots
target_exceedance = CPU_P95_EXCEEDANCE_TARGET / 100.0

if current_exceedance < target_exceedance:
    # Room in exceedance budget - can run high intensity
    intensity_decision = 1  # High intensity slot
else:
    # At budget limit - run at baseline
    intensity_decision = 0  # Normal intensity slot
```

#### Performance Optimizations
- **Ring buffer batching**: `CPU_P95_RING_BUFFER_BATCH_SIZE` (default: 10) batches state saves to reduce I/O frequency
- **Memory monitoring**: Tracks P95 cache memory usage with detailed logging for optimization
- **Database monitoring**: Monitors SQLite database size and growth patterns for capacity planning

#### Reliability Features
- **Configuration validation**: Cross-parameter consistency checks prevent invalid configurations
- **Database corruption detection**: PRAGMA quick_check with automatic backup and recovery
- **Graceful shutdown**: Ring buffer state saved on shutdown to prevent data loss

This approach ensures precise P95 positioning while maintaining Oracle compliance, system stability, and operational reliability.

### Network Fallback Controller Technical Details

The **NetworkFallbackState** provides intelligent network generation as a backup protection mechanism when CPU-based protection alone is insufficient.

#### Oracle Shape-Aware Logic
- **E2 shapes**: Activates when CPU P95 AND network both approach Oracle's 20% threshold
- **A1 shapes**: Activates when CPU P95, network, AND memory all approach Oracle's 20% threshold

#### State Management Architecture
```python
# Core activation logic based on Oracle reclamation rules
def should_activate(self, is_e2: bool, cpu_p95: float, net_avg: float, mem_avg: float):
    if is_e2:
        # E2: CPU + network criteria only
        cpu_at_risk = cpu_p95 < NET_FALLBACK_START_PCT
        net_at_risk = net_avg < NET_FALLBACK_RISK_THRESHOLD_PCT
        return cpu_at_risk and net_at_risk
    else:
        # A1: CPU + network + memory criteria
        cpu_at_risk = cpu_p95 < NET_FALLBACK_START_PCT
        net_at_risk = net_avg < NET_FALLBACK_RISK_THRESHOLD_PCT
        mem_at_risk = mem_avg < NET_FALLBACK_RISK_THRESHOLD_PCT
        return cpu_at_risk and net_at_risk and mem_at_risk
```

#### Anti-Oscillation Features
- **Debounce timing**: `NET_FALLBACK_DEBOUNCE_SEC` prevents rapid state changes
- **Minimum on/off periods**: Ensures stable activation cycles
- **EMA-based rate control**: Exponential moving average for stable network generation

#### Implementation Benefits
- **Oracle compliance**: Uses simple thresholds (not P95) for network measurements matching Oracle's method
- **Minimal impact**: Only generates traffic when multiple metrics simultaneously approach danger
- **Resource efficient**: Native Python socket-based network generation
- **Shape optimized**: Different activation logic for E2 vs A1 Oracle reclamation rules

## Project Structure & Module Organization
- `loadshaper.py` — single-service controller with internal worker processes that shapes CPU, RAM, and NIC load; reads config from environment; prints periodic telemetry. CPU stress must run at the lowest OS priority (`nice` 19) and yield quickly.
- `Dockerfile` — Python 3 Alpine image with minimal dependencies; runs `loadshaper.py`.
- `compose.yaml` — single service: `loadshaper` (client/loader) with configurable env vars; mounts config templates and persistent storage.
- `README.md`, `LICENSE` — usage and licensing.
- `CLAUDE.md` — additional guidance for Anthropic contributors; keep in sync with this file.
- `config-templates/` — Oracle shape-specific configuration templates (e2-1-micro.env, e2-2-micro.env, a1-flex-1.env, a1-flex-4.env) with optimized settings for each shape.

### Oracle Shape Auto-Detection
The system automatically detects Oracle Cloud shapes via:
- DMI system vendor detection (`/sys/class/dmi/id/sys_vendor`)
- Oracle-specific file presence (`/opt/oci-hpc`, `/etc/oci-hostname.conf`)
- CPU/memory characteristics analysis
- Shape-specific template loading with ENV > TEMPLATE > DEFAULT priority

## Build, Test, and Development Commands
- Build & run in Docker: `docker compose up -d --build`
- Tail logs: `docker logs -f loadshaper`
- Local run (Linux only, needs /proc): `python -u loadshaper.py`
- Override settings at launch, e.g.: `CPU_P95_SETPOINT=30 NET_PEERS=10.0.0.2,10.0.0.3 docker compose up -d`
- Custom storage location: `PERSISTENCE_DIR=/custom/path docker compose up -d`
- Run tests: `python -m pytest -q`

**🔗 See also:** [CONTRIBUTING.md](CONTRIBUTING.md) for detailed development setup and testing requirements.

## Coding Style & Naming Conventions
- Language: Python 3; 4‑space indentation; PEP 8 style.
- Names: functions/variables `snake_case`; constants `UPPER_SNAKE_CASE` (matches existing env-backed config).
- Keep dependencies minimal (standard library only). Avoid adding Python deps unless essential.
- Prefer small, testable helpers; keep I/O at edges; maintain clear separation between sensing, control, and workers.

## Testing Guidelines

### Unit Tests
- Run unit tests with `python -m pytest -q`.
- Add tests for any new utility functions or control logic.
- Test edge cases (negative values, missing files, network failures).

### Performance Feature Testing
**Ring Buffer Batching**: Test I/O optimization with different `CPU_P95_RING_BUFFER_BATCH_SIZE` values:
- Verify batched saves reduce file I/O frequency
- Test performance impact of different batch sizes (1, 10, 100)
- Validate state persistence accuracy with batching
- Test updated mock patterns handle new thread-safe temp file naming (`{path}.{pid}.{thread_id}.tmp`)

**Thread Safety**: Test race condition prevention in ring buffer operations:
- Verify PID+thread temp files prevent concurrent write conflicts
- Test proper locking during ring buffer state saves
- Validate atomic file operations across multiple threads

**ENOSPC Degraded Mode**: Test comprehensive disk full scenario handling:
- Test ENOSPC error detection triggers degraded mode properly
- Verify degraded mode skips persistence operations to prevent crash loops
- Test manual recovery from degraded mode works correctly

**Configuration Validation**: Test cross-parameter consistency checks:
- Verify invalid combinations are detected (e.g., CPU_P95_TARGET_MIN > CPU_P95_TARGET_MAX)
- Test warning messages for suboptimal configurations
- Validate Oracle shape-specific configuration constraints
- Test configuration validation timing occurs after runtime initialization

**Database Corruption Handling**: Test detection and recovery mechanisms:
- Simulate database corruption and verify detection
- Test automatic backup creation before recovery attempts
- Validate successful recovery from backup files

### Integration Testing
- Validate behavior by running the stack and observing `[loadshaper]` telemetry.
- CPU/RAM only: `NET_MODE=off docker compose up -d`.
- Network shaping is a fallback; set peers (comma-separated IPs) via `NET_PEERS` to establish TCP connections on `NET_PORT`.

### Memory Occupation Testing
**For A1.Flex shapes (memory reclamation applies):**
- Verify memory allocation increases when `mem(excl-cache)` is below `MEM_TARGET_PCT`
- Test memory touching frequency (`MEM_TOUCH_INTERVAL_SEC`, default: 1.0 second)
- Monitor RSS and VSZ to confirm memory is actually consumed and resident
- Test with different `MEM_STEP_MB` values (64MB default for gradual allocation)
- Verify memory touching pauses when `LOAD_THRESHOLD` exceeded
- Test page touching efficiency with different page sizes
- **Memory Calculation Validation**: Enable `DEBUG_MEM_METRICS=true` to compare both metric calculations
- **Oracle Monitoring Comparison**: If available, compare with Oracle's Instance Monitoring memory metrics

### Load Average Monitoring
- Test with `LOAD_THRESHOLD=0.1` to verify workers pause under light load
- Simulate CPU contention with `stress` or similar tools
- Verify hysteresis works (no oscillation between pause/resume)

### CPU Load Responsiveness Testing
**Critical requirement: CPU load must have minimal impact on system responsiveness**

- **Latency impact testing**: Measure response times of simple system operations (file creation, network pings) with and without loadshaper running
- **Context switching overhead**: Monitor context switch rates using `vmstat` or `/proc/stat` to ensure minimal increase
- **Priority validation**: Confirm CPU workers immediately yield to higher-priority processes
- **Yielding behavior**: Test that 5ms sleep slices provide adequate yielding under various system loads
- **Cache impact**: Verify CPU stress workload doesn't significantly impact cache performance for other processes
- **I/O responsiveness**: Ensure disk and network I/O from other processes remains responsive

**Test scenarios:**
- Run loadshaper alongside typical server workloads (web server, database queries)
- Measure latency percentiles (P50, P95, P99) for critical operations
- Compare system responsiveness metrics before/during/after loadshaper execution
- Validate immediate pausing when legitimate high-priority work appears

### 7-Day Metrics Validation  
- Confirm database storage works: `docker exec loadshaper sqlite3 /var/lib/loadshaper/metrics.db ".tables" || echo "Database not found"`
- Check percentile calculations with: `docker exec loadshaper sqlite3 /var/lib/loadshaper/metrics.db "SELECT COUNT(*) FROM metrics;" || echo "Database not found"`
- Verify cleanup removes old data properly

### Shape-Specific Testing
**E2.1.Micro (x86, 1/8 OCPU):**
- Default targets should be conservative (CPU≤25%, no memory pressure)
- Network should focus on external internet traffic
- Load thresholds should be lower (more sensitive to contention)

**A1.Flex (ARM, flexible):**
- Test memory occupation effectiveness with higher targets (40-60%)
- Verify per-vCPU network scaling works
- Test with multiple vCPU configurations
- Validate memory touch intervals work correctly (0.5-10.0 second range)

### Safety Checks
- Verify `*_STOP_PCT` thresholds trigger pause/resume correctly
- Test emergency shutdown scenarios (SIGTERM, container stop)
- Include log snippets in PRs showing safety mechanisms working

### Performance Benchmarks

**Resource Usage Baseline (when idle):**
```bash
# Measure loadshaper's own resource consumption
docker stats loadshaper --no-stream
# Expected: <1% CPU, 10-20MB memory when not actively generating load
```

**Responsiveness Testing:**
```bash
# Test system responsiveness with loadshaper running
time ls -la /usr/bin/  # Should be <100ms
ping -c 5 8.8.8.8     # Should show normal latency
```

**Load Generation Effectiveness:**
```bash
# Verify CPU load reaches targets
docker logs loadshaper | grep -E "cpu now=[0-9.]+" | tail -10

# Check CPU 95th percentile calculations  
docker exec loadshaper sqlite3 /var/lib/loadshaper/metrics.db \
  "SELECT resource_type, COUNT(*), 
   ROUND(AVG(value), 2) as avg_value,
   ROUND(
     (SELECT value FROM metrics m2 
      WHERE m2.resource_type = m1.resource_type 
      ORDER BY value DESC 
      LIMIT 1 OFFSET CAST(COUNT(*) * 0.05 as INTEGER)
     ), 2
   ) as p95_value
   FROM metrics m1 
   WHERE timestamp > datetime('now', '-7 days') 
   GROUP BY resource_type;"
```

### Continuous Testing Strategy

**Pre-commit checks:**
```bash
# Run all tests
python -m pytest -q

# Verify container builds
docker compose build

# Check configuration consistency
python -c "import loadshaper; print('Import successful')"
```

**Integration test workflow:**
```bash
# 1. Start with clean state
docker compose down -v
docker compose up -d --build

# 2. Wait for startup and check health
sleep 30
docker logs loadshaper | tail -10

# 3. Verify metrics collection and mount detection
docker exec loadshaper sqlite3 /var/lib/loadshaper/metrics.db ".tables" 2>/dev/null || echo "Persistent storage not mounted - container will fail"
docker logs loadshaper | grep -E "(persistence|mount|device)" | head -5  # Check mount detection logs

# 4. Test different load scenarios
LOAD_THRESHOLD=0.1 docker compose up -d  # Should pause quickly
LOAD_THRESHOLD=2.0 docker compose up -d  # Should rarely pause

# 5. Cleanup
docker compose down
```

## External Contribution Guidelines

### Welcome Contributors
We welcome contributions to `loadshaper`! This project helps Oracle Cloud Always Free users prevent VM reclamation through intelligent resource management.

### Development Setup
```bash
git clone https://github.com/senomorf/loadshaper.git
cd loadshaper

# Create Python virtual environment (required)
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# Install development dependencies
pip install -e .  # If setup.py exists
# or just run: python3 -u loadshaper.py

# Run tests
python -m pytest -q

# Test with Docker
docker compose up -d --build
docker logs -f loadshaper
```

### Types of Contributions
- **Bug fixes**: Issues with resource monitoring, load generation, or safety systems
- **Platform support**: Additional Oracle Cloud shapes or other cloud providers  
- **Performance improvements**: Better algorithms, reduced overhead, smarter yielding
- **Testing**: Additional test cases, especially for edge cases and different hardware
- **Documentation**: README improvements, code comments, troubleshooting guides

### Contribution Workflow
1. **Fork** the repository and create a feature branch
2. **Implement** your changes following the coding style guidelines
3. **Test** thoroughly with both unit tests and manual verification
4. **Document** any new environment variables or behavior changes
5. **Submit** a pull request with detailed description

### Release Process

**Version Strategy**: 
- Semantic versioning (MAJOR.MINOR.PATCH)
- Tag releases with `git tag v1.2.3`
- Keep CHANGELOG.md updated

**Release Steps**:
1. Update version references in documentation
2. Test on both E2.1.Micro and A1.Flex instances  
3. Verify Docker builds on multiple architectures
4. Create GitHub release with release notes
5. Update any deployment documentation

## Commit & Pull Request Guidelines
- Commits: clear, imperative subject lines; mention touched subsystems (cpu, mem, net, compose, docs) and key env vars.
- PRs must include: summary, rationale, config/env changes, manual test steps, and before/after telemetry screenshots or log lines.
- Update `README.md` for user-facing changes (flags, env vars, run instructions).

## Security & Configuration Tips
- Host NIC sensing (`NET_SENSE_MODE=host`) expects `/sys/class/net` bind-mounted to `/host_sys_class_net`; otherwise use `container` mode with `NET_LINK_MBIT`.
- Use unprivileged `NET_PORT` (default 15201). Be cautious raising `MEM_STOP_PCT` or `NET_MAX_RATE_MBIT` on shared hosts.

