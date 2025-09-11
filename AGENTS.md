# Repository Guidelines

## Architecture Overview

`loadshaper` is designed as a single-process monitoring and load generation system with clear separation of concerns:

### Core Components
- **Metric Collection**: System-level monitoring (CPU, memory, network, load average)
- **Storage Layer**: SQLite-based 7-day rolling metrics with 95th percentile calculations
- **Control Logic**: PID-style controllers for each resource type with hysteresis
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
│ (7-day p95) │    │ (Load Avg)  │    │ (Logs/UI)   │
└─────────────┘    └─────────────┘    └─────────────┘
```

### Design Principles
- **Unobtrusive**: Always yields to legitimate workloads (nice 19 priority)
- **Adaptive**: Responds to system load and Oracle's reclamation criteria
- **Resilient**: Handles storage failures, network issues, and system restarts
- **Observable**: Rich telemetry for monitoring and debugging

## Project Structure & Module Organization
- `loadshaper.py` — single-process controller that shapes CPU, RAM, and NIC load; reads config from environment; prints periodic telemetry. CPU stress must run at the lowest OS priority (`nice` 19) and yield quickly.
- `Dockerfile` — Python 3 Alpine image with `iperf3`; runs `loadshaper.py`.
- `compose.yaml` — two services: `loadshaper` (client/loader) and `iperf3` (receiver) with configurable env vars.
- `README.md`, `LICENSE` — usage and licensing.
- `CLAUDE.md` — additional guidance for Anthropic contributors; keep in sync with this file.

## Build, Test, and Development Commands
- Build & run in Docker: `docker compose up -d --build`
- Tail logs: `docker logs -f loadshaper`
- Local run (Linux only, needs /proc): `python -u loadshaper.py`
- Override settings at launch, e.g.: `CPU_TARGET_PCT=35 NET_PEERS=10.0.0.2,10.0.0.3 docker compose up -d`
- Run tests: `python -m pytest -q`

## Coding Style & Naming Conventions
- Language: Python 3; 4‑space indentation; PEP 8 style.
- Names: functions/variables `snake_case`; constants `UPPER_SNAKE_CASE` (matches existing env-backed config).
- Keep dependencies minimal (standard library + `iperf3` binary). Avoid adding Python deps unless essential.
- Prefer small, testable helpers; keep I/O at edges; maintain clear separation between sensing, control, and workers.

## Testing Guidelines

### Unit Tests
- Run unit tests with `python -m pytest -q`.
- Add tests for any new utility functions or control logic.
- Test edge cases (negative values, missing files, network failures).

### Integration Testing
- Validate behavior by running the stack and observing `[loadshaper]` telemetry.
- CPU/RAM only: `NET_MODE=off docker compose up -d`.
- Network shaping is a fallback; set peers (comma-separated IPs) via `NET_PEERS` and ensure peers run an iperf3 server on `NET_PORT`.

### Memory Stressor Testing
**For A1.Flex shapes (memory reclamation applies):**
- Verify memory allocation increases when `mem(no-cache)` is below `MEM_TARGET_PCT`
- Test memory touching frequency (current: every 1 second)
- Monitor RSS and VSZ to confirm memory is actually consumed
- Test with different `MEM_STEP_MB` values (64MB default may be too small)

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
- Test memory stressor effectiveness with higher targets (40-60%)
- Verify per-vCPU network scaling works
- Test with multiple vCPU configurations

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

# Check 95th percentile calculations  
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

# 3. Verify metrics collection
docker exec loadshaper sqlite3 /var/lib/loadshaper/metrics.db ".tables" 2>/dev/null || echo "Using fallback storage"

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

