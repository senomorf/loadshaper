# Repository Guidelines

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

## Commit & Pull Request Guidelines
- Commits: clear, imperative subject lines; mention touched subsystems (cpu, mem, net, compose, docs) and key env vars.
- PRs must include: summary, rationale, config/env changes, manual test steps, and before/after telemetry screenshots or log lines.
- Update `README.md` for user-facing changes (flags, env vars, run instructions).

## Security & Configuration Tips
- Host NIC sensing (`NET_SENSE_MODE=host`) expects `/sys/class/net` bind-mounted to `/host_sys_class_net`; otherwise use `container` mode with `NET_LINK_MBIT`.
- Use unprivileged `NET_PORT` (default 15201). Be cautious raising `MEM_STOP_PCT` or `NET_MAX_RATE_MBIT` on shared hosts.

