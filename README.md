# loadshaper

![Oracle Cloud](https://img.shields.io/badge/Oracle%20Cloud-F80000?style=for-the-badge&logo=oracle&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-blue.svg)
![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)
![Docker](https://img.shields.io/badge/Docker-supported-blue.svg)
![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20ARM64%20%7C%20x86--64-lightgrey.svg)

## ⚠️ Work In Progress - Breaking Changes Expected

> **🚧 This project is under active development.** Breaking changes are introduced frequently
> without migration paths. This is intentional as we iterate toward the optimal solution for
> preventing Oracle Cloud VM reclamation. Always review the CHANGELOG before updating.
> Current version requires **Linux 3.14+ (March 2014)** - older kernel support has been removed.

### Oracle Cloud Always Free VM Keeper
**Intelligent baseline load generator that prevents Oracle Cloud Always Free compute instances from being reclaimed due to underutilization.**

## Problem

Oracle Cloud Always Free compute instances are automatically reclaimed if they remain underutilized for 7 consecutive days. An instance is considered idle when **ALL** of the following conditions are met over a 7-day window:

- **CPU utilization for the 95th percentile** is below 20%
- **Network utilization** is below 20% (simple threshold, not P95)
- **Memory utilization** is below 20% (A1.Flex shapes only, simple threshold, not P95)

**Source**: [Oracle Cloud Always Free Resources - Idle Compute Instances](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm#compute__idleinstances)

## Solution

`loadshaper` prevents VM reclamation by intelligently maintaining resource utilization above Oracle's thresholds while remaining completely unobtrusive to legitimate workloads. It:

✅ **Keeps at least one metric above 20%** to prevent reclamation  
✅ **Runs at lowest OS priority** (nice 19) with minimal system impact  
✅ **Automatically pauses** when real workloads need resources  
✅ **Tracks CPU 95th percentile** over 7-day rolling windows (matches Oracle's measurement)  
✅ **Works on both x86-64 and ARM64** Oracle Free Tier shapes

## Quick Start

**1. Clone and deploy:**
```bash
git clone https://github.com/senomorf/loadshaper.git
cd loadshaper
docker compose up -d --build
```

**2. Monitor activity:**
```bash
docker logs -f loadshaper
```

**3. See current metrics:**
```bash
# Look for telemetry lines showing current, average, and 95th percentile values
docker logs loadshaper | grep "\[loadshaper\]" | tail -5
```

That's it! `loadshaper` will automatically detect your Oracle Cloud shape and start maintaining appropriate resource utilization.

**📖 More Information:**
- [Configuration Reference](#configuration-reference) - Detailed environment variable options
- [CONTRIBUTING.md](CONTRIBUTING.md) - Development setup and contribution guidelines
- [CHANGELOG.md](CHANGELOG.md) - Version history and breaking changes

## Oracle Free Tier thresholds

Oracle's Always Free Tier compute shapes have the following specifications and reclamation thresholds:

**VM.Standard.E2.1.Micro:**
- CPU: 1/8 OCPU (burstable)
- Memory: 1 GB RAM (no memory reclamation rule)
- Network: 480 Mbps internal, **50 Mbps external (internet)**
- 20% threshold = ~10 Mbps external traffic required

**A1.Flex (ARM-based):**
- CPU: Up to 4 OCPUs
- Memory: Up to 24 GB RAM (20% threshold applies)
- Network: 1 Gbps per vCPU
- 20% threshold = ~0.2 Gbps per vCPU required

**Important:** Network monitoring typically measures internet-bound traffic (external bandwidth), not internal VM-to-VM traffic. For E2 shapes, focus on the 50 Mbps external limit.

`loadshaper` drives CPU usage and can emit network traffic. It tracks CPU utilization over a 7-day rolling window and calculates the 95th percentile to match Oracle's exact reclamation criteria. For memory and network, it uses simple threshold monitoring (no P95) as per Oracle's actual measurement method. The telemetry output shows current values, averages, and CPU P95.

## Architecture

`loadshaper` operates as a lightweight monitoring and control system with three main components:

### 1. **Metric Collection**
- **CPU utilization**: Read from `/proc/stat` (system-wide percentage)
- **Memory utilization**: Read from `/proc/meminfo` using industry-standard calculation (see [Memory Calculation](#memory-calculation))
- **Network utilization**: Read from `/proc/net/dev` with automatic speed detection
- **Load average**: Monitor from `/proc/loadavg` to detect CPU contention

### 2. **7-Day Metrics Storage**
- **SQLite database**: Stores samples every 5 seconds for rolling 7-day analysis
- **95th percentile calculation**: CPU only (mirrors Oracle's measurement method)
- **Automatic cleanup**: Removes data older than 7 days
- **Storage locations**: `/var/lib/loadshaper/metrics.db` (preferred) or `/tmp/loadshaper_metrics.db` (fallback)

### 3. **Intelligent Load Generation**
- **CPU stress**: Low-priority workers (nice 19) with arithmetic operations
- **Memory occupation**: Gradual allocation with periodic page touching for A1.Flex shapes  
- **Network traffic**: iperf3-based bursts to peer instances when needed
- **Load balancing**: Automatic pausing when real workloads need resources

### Operation Flow
```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Collect       │───▶│   Analyze       │───▶│   Adjust        │
│   Metrics       │    │   CPU P95       │    │   Load Level    │
│   Every 5s      │    │   vs Thresholds │    │   Accordingly   │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Store in      │    │   Check Load    │    │   Yield to      │
│   SQLite DB     │    │   Average for   │    │   Real Work     │
│   (7 days)      │    │   Contention    │    │   When Needed   │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

## CPU load characteristics

**Design Priority: Minimal Impact on System Responsiveness**

CPU stress runs at the **absolute lowest OS priority** (`nice` 19) and is designed to have minimal impact on system responsiveness for other processes. Key characteristics:

- **Lowest priority**: Both controller and CPU workers run at `nice` 19, immediately yielding to any real workloads
- **Transient bursts**: Short, jittered activity periods with frequent sleep intervals (5ms yielding slices)
- **Baseline operation**: Designed to be lightweight background activity, not sustained high-intensity load
- **Immediate yielding**: Automatically pauses when system load average indicates CPU contention from legitimate processes

**Workload Selection Criteria**: When choosing between stress methods that produce similar CPU utilization metrics, always prioritize the approach with the **least impact on system responsiveness and latency** for other processes. The current implementation uses simple arithmetic operations that minimize context switching overhead and avoid cache pollution.

## Network shaping as fallback

Network traffic should only be generated when CPU activity risks falling below Oracle's 20% threshold. Since Oracle uses simple threshold monitoring (not P95) for network utilization, loadshaper can use basic averaging to maintain network levels when needed as a fallback to CPU-based protection.

## Load average monitoring

`loadshaper` monitors system load average to detect CPU contention from other
processes. When the 1-minute load average per core exceeds the configured 
threshold (default 0.6), CPU workers are automatically paused to yield resources
to legitimate workloads. Workers resume when load drops below the resume 
threshold (default 0.4). This ensures `loadshaper` remains unobtrusive and
immediately steps aside when real work needs the CPU.

The default thresholds (0.6/0.4) provide a good balance between responsiveness
to legitimate workloads and stability. The hysteresis gap prevents oscillation
when load hovers near the threshold.

## 7-day metrics storage

`loadshaper` automatically stores CPU, memory, and network utilization samples
in a lightweight SQLite database for 7-day rolling analysis. Metrics are stored
at each control period (default 5 seconds) and automatically cleaned up after
7 days.

**Storage location:**
- Primary: `/var/lib/loadshaper/metrics.db` (if writable)
- Fallback: `/tmp/loadshaper_metrics.db`

**Telemetry output format:**
```
[loadshaper] cpu now=45.2% avg=42.1% p95=48.3% | mem(excl-cache) now=55.1% avg=52.8% | nic(...) now=12.50% avg=11.25% | load now=0.45 avg=0.42 | ... | samples_7d=98547
```

Where:
- `now`: Current sample value
- `avg`: 5-minute exponential moving average (memory/network only; CPU uses P95 control)
- `p95`: 95th percentile over the past 7 days (CPU only, matches Oracle's measurement)
- `samples_7d`: Number of samples stored in the 7-day window

**Storage characteristics:**
- Approximately 120,960 samples per week (one every 5 seconds)
- Estimated database size: 10-20 MB for 7 days of data
- Thread-safe for concurrent access
- Gracefully handles storage failures (continues with existing behavior)

## Overriding detection and thresholds

Environment variables can override shape detection and contention limits:

```shell
# Override detected NIC speed and adjust network caps
NET_SENSE_MODE=container NET_LINK_MBIT=10000 NET_STOP_PCT=20 python -u loadshaper.py

# Raise CPU target while lowering the safety stop
CPU_P95_SETPOINT=50.0 CPU_STOP_PCT=70 MEM_TARGET_PCT=25 MEM_STOP_PCT=80 python -u loadshaper.py

# Configure load average monitoring thresholds (more aggressive example)
LOAD_THRESHOLD=1.0 LOAD_RESUME_THRESHOLD=0.6 LOAD_CHECK_ENABLED=true python -u loadshaper.py

# Conservative load monitoring (earlier pause, safer for shared systems)
LOAD_THRESHOLD=0.4 LOAD_RESUME_THRESHOLD=0.2 python -u loadshaper.py
```

## Oracle Shape Auto-Detection

`loadshaper` automatically detects your Oracle Cloud shape and applies optimized configuration templates:

### Supported Shapes

| Shape | CPU | RAM | Network | Template |
|-------|-----|-----|---------|----------|
| **VM.Standard.E2.1.Micro** | 1/8 OCPU | 1GB | 50 Mbps | `e2-1-micro.env` |
| **VM.Standard.E2.2.Micro** | 2/8 OCPU | 2GB | 50 Mbps | `e2-2-micro.env` |
| **VM.Standard.A1.Flex** (1 vCPU) | 1 vCPU | 6GB | 1 Gbps | `a1-flex-1.env` |
| **VM.Standard.A1.Flex** (2 vCPU) | 2 vCPU | 12GB | 2 Gbps | `a1-flex-2.env` |
| **VM.Standard.A1.Flex** (3 vCPU) | 3 vCPU | 18GB | 3 Gbps | `a1-flex-3.env` |
| **VM.Standard.A1.Flex** (4 vCPU) | 4 vCPU | 24GB | 4 Gbps | `a1-flex-4.env` |

### Configuration Priority

The system uses a three-tier configuration priority:
1. **Environment Variables** (highest priority)
2. **Shape-specific Template** (automatic detection)
3. **Built-in Defaults** (conservative fallback)

This means you can override any template value with environment variables while still benefiting from automatic shape-optimized defaults.

### Non-Oracle Environments

For non-Oracle Cloud environments, `loadshaper` safely falls back to conservative E2.1.Micro-like defaults, making it safe to run anywhere.

## Memory Calculation

### Why We Exclude Cache/Buffers

**Critical for Oracle compliance**: `loadshaper` calculates memory utilization by **excluding cache/buffers**, which aligns with industry standards and Oracle's likely implementation for VM reclamation criteria.

**The Problem with Including Cache/Buffers:**
- Linux aggressively uses free RAM for disk caching (often 50-80% of total memory)
- This cache is instantly reclaimable when applications need memory
- Including cache would make almost every Linux VM appear "active" even when idle
- This would defeat Oracle's reclamation policy of finding truly unused VMs

**Industry Standard Approach:**
- **AWS CloudWatch**: `mem_used_percent` excludes cache/buffers
- **Azure Monitor**: Uses "available memory" metrics (cache-aware)  
- **Kubernetes**: Uses "working set" memory (excludes reclaimable cache)
- **Google Cloud**: Uses similar cache-aware calculations

### Calculation Methods

**Preferred Method (Linux 3.14+):**
```
memory_utilization = 100 × (1 - MemAvailable/MemTotal)
```

**Fallback Method (older kernels):**
```
cache_buffers = Buffers + max(0, Cached + SReclaimable - Shmem)
memory_utilization = 100 × (MemTotal - MemFree - cache_buffers) / MemTotal
```

### Debugging Memory Metrics

Set `DEBUG_MEM_METRICS=true` to see both calculations in telemetry:
```bash
DEBUG_MEM_METRICS=true docker compose up -d
```

Output example:
```
mem(excl-cache) now=25.3% avg=24.1% p95=28.7% [incl-cache=78.2%]
```

This shows the huge difference: 25% (real app usage) vs 78% (including cache).

## Configuration Reference

> **⚠️ CRITICAL:** For Oracle Free Tier VM protection, ensure **at least one metric target is above 20%**. Setting all targets below 20% will cause Oracle to reclaim your VM. Oracle checks if ALL metrics are below 20% - if so, the VM is reclaimed.

### Resource Targets

| Variable | Auto-Configured Values | Description | E2.1.Micro | E2.2.Micro | A1.Flex-1 | A1.Flex-2 | A1.Flex-3 | A1.Flex-4 |
|----------|---------|-------------|------------|------------|------------|------------|------------|------------|
| `CPU_P95_SETPOINT` | **23.5**, 28.5, 28.5, 28.5, 28.5, 30.0 | Target CPU P95 (7-day window) | 23.5% | 28.5% | 28.5% | 28.5% | 28.5% | 30.0% |
| `MEM_TARGET_PCT` | **0**, 0, 30, 30, 30, 30 | Target memory utilization (%) | 0% (disabled) | 0% (disabled) | 30% (above 20% rule) | 30% (above 20% rule) | 30% (above 20% rule) | 30% (above 20% rule) |
| `NET_TARGET_PCT` | **15**, 15, 25, 25, 25, 30 | Target network utilization (%) | 15% (50 Mbps) | 15% (50 Mbps) | 25% (1 Gbps) | 25% (2 Gbps) | 25% (3 Gbps) | 30% (4 Gbps) |

### Safety Thresholds

| Variable | Auto-Configured Values | Description | E2 Shapes | A1 Shapes |
|----------|---------|-------------|-----------|-----------|
| `CPU_STOP_PCT` | **45**, 50, 85, 85 | CPU % to pause load generation | 45-50% (shared tenancy) | 85% (dedicated) |
| `MEM_STOP_PCT` | **80**, 85, 90, 90 | Memory % to pause allocation | 80-85% (conservative) | 90% (with 20% rule) |
| `NET_STOP_PCT` | **40**, 40, 60, 60 | Network % to pause traffic | 40% (50 Mbps limit) | 60% (higher capacity) |

### Control Behavior

| Variable | Default | Description |
|----------|---------|-------------|
| `CONTROL_PERIOD_SEC` | `5` | Seconds between control decisions |
| `AVG_WINDOW_SEC` | `300` | Exponential moving average window for memory/network (5 min) |
| `HYSTERESIS_PCT` | `5` | Percentage hysteresis to prevent oscillation |
| `JITTER_PCT` | `15` | Random jitter in load generation (%) |
| `JITTER_PERIOD_SEC` | `5` | Seconds between jitter adjustments |

### P95 Controller Configuration

**⚠️ CRITICAL**: These variables control Oracle's 95th percentile CPU measurement that determines reclamation. CPU P95 must stay above 20% for Oracle Free Tier protection.

| Variable | Default | Description |
|----------|---------|-------------|
| `CPU_P95_TARGET_MIN` | `22.0` | Minimum target for 7-day CPU P95 (must stay >20%) |
| `CPU_P95_TARGET_MAX` | `28.0` | Maximum target for 7-day CPU P95 (efficiency ceiling) |
| `CPU_P95_SETPOINT` | `25.0` | Optimal P95 target (center of safe range 22-28%) |
| `CPU_P95_EXCEEDANCE_TARGET` | `6.5` | Target percentage of high-intensity slots (%) |
| `CPU_P95_SLOT_DURATION_SEC` | `60.0` | Duration of each control slot (seconds) |
| `CPU_P95_HIGH_INTENSITY` | `35.0` | CPU utilization during high-intensity slots (%) |
| `CPU_P95_BASELINE_INTENSITY` | `20.0` | CPU utilization during normal slots (minimum for Oracle compliance) |

### Load Average Monitoring

| Variable | Default | Description |
|----------|---------|-------------|
| `LOAD_THRESHOLD` | `0.6` | Load average per core to pause workers |
| `LOAD_RESUME_THRESHOLD` | `0.4` | Load average per core to resume workers |
| `LOAD_CHECK_ENABLED` | `true` | Enable/disable load average monitoring |

### Memory Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MEM_MIN_FREE_MB` | `512` | Minimum free memory to maintain (MB) |
| `MEM_STEP_MB` | `64` | Memory allocation step size (MB) |
| `MEM_TOUCH_INTERVAL_SEC` | `1.0` | Memory page touching frequency (0.5-10.0 seconds) |

### Network Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `NET_MODE` | `client` | Network mode: `off`, `client` |
| `NET_PROTOCOL` | `udp` | Protocol: `udp` (lower CPU), `tcp` |
| `NET_PEERS` | `10.0.0.2,10.0.0.3` | Comma-separated peer IP addresses or hostnames |
| `NET_PORT` | `15201` | iperf3 port for communication |
| `NET_BURST_SEC` | `10` | Duration of traffic bursts (seconds) |
| `NET_IDLE_SEC` | `10` | Idle time between bursts (seconds) |

### Network Interface Detection

| Variable | Default | Description |
|----------|---------|-------------|
| `NET_SENSE_MODE` | `container` | Detection mode: `container`, `host` |
| `NET_IFACE_INNER` | `eth0` | Container interface name |
| `NET_LINK_MBIT` | `1000` | Fallback link speed (Mbps) |
| `NET_MIN_RATE_MBIT` | `1` | Minimum traffic generation rate |
| `NET_MAX_RATE_MBIT` | `800` | Maximum traffic generation rate |

### Shape-Specific Recommendations

**VM.Standard.E2.1.Micro (x86-64):**
```bash
# Conservative settings for shared 1/8 OCPU
CPU_P95_SETPOINT=23.5 MEM_TARGET_PCT=0 NET_TARGET_PCT=15
NET_LINK_MBIT=50 LOAD_THRESHOLD=0.6
```

**A1.Flex (ARM64):**
```bash  
# Higher targets for dedicated resources
CPU_P95_SETPOINT=28.5 MEM_TARGET_PCT=25 NET_TARGET_PCT=25
NET_LINK_MBIT=1000 LOAD_THRESHOLD=0.8
```

### Health Check Server

| Variable | Default | Description |
|----------|---------|-------------|
| `HEALTH_ENABLED` | `true` | Enable/disable HTTP health check server |
| `HEALTH_PORT` | `8080` | Port for health check endpoints |
| `HEALTH_HOST` | `127.0.0.1` | Host interface to bind (localhost only by default) |
| `LOADSHAPER_TEMPLATE_DIR` | `config-templates/` | Directory containing Oracle shape configuration templates |
| `ORACLE_METADATA_PROBE` | `0` | Enable Oracle-specific metadata service probe (0=disabled, 1=enabled) |

### Shape Detection Cache

Oracle shape detection results are cached for 5 minutes (300 seconds) to avoid repeated system calls. The cache includes:
- Detected shape name and template file
- Oracle environment detection result
- System specifications (CPU count, memory size)

**Note**: In containerized environments, memory detection reflects the host system, not container limits.

## Health Check Endpoints

`loadshaper` provides HTTP endpoints for health monitoring and metrics retrieval, primarily designed for Docker container health checks and monitoring systems.

### Endpoints

**`GET /health`** - Health check status
```json
{
  "status": "healthy",
  "uptime_seconds": 3245.1,
  "timestamp": 1705234567.89,
  "checks": ["all_systems_operational"],
  "metrics_storage": "available",
  "load_generation": "active"
}
```

**`GET /metrics`** - Detailed metrics and configuration
```json
{
  "timestamp": 1705234567.89,
  "current": {
    "cpu_percent": 45.2,
    "cpu_avg": 42.1,
    "memory_percent": 55.1,
    "memory_avg": 52.8,
    "network_percent": 12.5,
    "network_avg": 11.25,
    "load_average": 0.42,
    "duty_cycle": 0.65,
    "network_rate_mbit": 15.2,
    "paused": false
  },
  "targets": {
    "cpu_target": 25.0,
    "memory_target": 0.0,
    "network_target": 15.0
  },
  "percentiles_7d": {
    "cpu_p95": 48.3,
    "sample_count_7d": 98547
  }
}
```

### Docker Integration

Add health check to your Docker compose or Dockerfile:

```yaml
# docker-compose.yml
services:
  loadshaper:
    build: .
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
```

```dockerfile
# Dockerfile
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1
```

### Security Configuration

By default, the health server binds to localhost only (`127.0.0.1`) for security. To enable external access:

```bash
# Allow access from any interface (Docker containers)
HEALTH_HOST=0.0.0.0 docker run ...

# Bind to specific interface
HEALTH_HOST=10.0.0.1 docker run ...

# Disable health server entirely
HEALTH_ENABLED=false docker run ...
```

**Security Note**: Only bind to external interfaces (`0.0.0.0`) in trusted environments or behind proper network security controls.

## FAQ

### General Questions

**Q: Will this impact the performance of my applications?**  
A: No. `loadshaper` runs at the lowest OS priority (nice 19) and automatically pauses when real workloads need resources. It's designed to be completely invisible to legitimate applications.

**Q: How much system resources does loadshaper use?**  
A: Very minimal - typically <1% CPU when idle, 10-20MB memory for metrics storage, and network traffic only when needed as fallback.

**Q: Does this work on both x86-64 and ARM64?**  
A: Yes, it automatically detects and adapts to both VM.Standard.E2.1.Micro (x86-64) and A1.Flex (ARM64) shapes.

### Oracle Cloud Specific

**Q: How does this prevent my Always Free instance from being reclaimed?**  
A: Oracle reclaims instances when ALL metrics are below 20% for 7 days. `loadshaper` ensures at least one metric stays above 20% by tracking CPU 95th percentile (matching Oracle's measurement) and simple averages for memory/network.

**Q: What if Oracle changes their reclamation policy?**  
A: The thresholds are easily configurable via environment variables. Simply adjust `CPU_P95_SETPOINT`, `MEM_TARGET_PCT`, or `NET_TARGET_PCT` as needed.

**Q: Will Oracle consider this usage "legitimate"?**  
A: The tool generates actual resource utilization that would be visible to Oracle's monitoring. However, you should review Oracle's terms of service to ensure compliance with your use case.

### Technical Questions

**Q: Why does memory targeting default to 0% on E2.1.Micro?**  
A: E2 shapes only have 1GB RAM and memory isn't counted in Oracle's reclamation criteria for these instances. Memory targeting is only enabled by default on A1.Flex shapes.

**Q: How can I tell if it's working?**
A: Watch the telemetry output: `docker logs -f loadshaper`. You'll see current, average, and CPU 95th percentile values.

**Q: What happens if I restart the container?**  
A: Metrics history is preserved in the SQLite database (stored in `/var/lib/loadshaper/` or `/tmp/`). The 7-day rolling window continues from where it left off.

**Q: Can I run this alongside other applications?**  
A: Absolutely. That's the primary use case. `loadshaper` is designed to coexist peacefully with any workload.

### Troubleshooting

**Q: CPU load isn't reaching the target percentage**  
A: Check if `LOAD_THRESHOLD` is too low (causing frequent pauses) or if `CPU_STOP_PCT` is being triggered. Try increasing `LOAD_THRESHOLD` to 0.8 or 1.0.

**Q: Network traffic isn't being generated**  
A: Ensure you have `NET_MODE=client` and valid `NET_PEERS` IP addresses. Verify iperf3 servers are running on peer instances and firewall rules allow traffic on `NET_PORT`.

**Q: Memory usage isn't increasing on A1.Flex**  
A: Check available free memory and ensure `MEM_TARGET_PCT` is set above current usage. Verify the container has adequate memory limits.

## Contributing

Interested in improving `loadshaper`? Check out our [Contributing Guide](CONTRIBUTING.md) for:
- Development environment setup
- Testing requirements
- Code style guidelines
- How to submit improvements

## Testing

Loadshaper includes comprehensive test coverage to ensure reliability:

### Running Tests
```bash
# Run all tests
python -m pytest -q

# Run specific test modules
python -m pytest tests/test_cpu_p95_controller.py -v
python -m pytest tests/test_health_endpoints.py -v

# Run with coverage
python -m pytest --cov=loadshaper
```

### Test Strategy

**CPUP95Controller Test Suite** (`tests/test_cpu_p95_controller.py`):
- **46 comprehensive tests** covering all controller functionality
- **Initialization**: Ring buffer sizing, cache behavior, state setup
- **State Machine**: BUILDING/MAINTAINING/REDUCING transitions with adaptive hysteresis
- **Intensity Calculations**: Target intensity algorithms for different states and P95 distances
- **Exceedance Targets**: Adaptive exceedance budget control based on state and P95 deviation
- **Slot Engine**: Time-based slot rollover, exceedance budget control, safety gating
- **Status Reporting**: Telemetry data structure and accuracy
- **Edge Cases**: Extreme configurations, error conditions, boundary conditions

**Health Endpoints** (`tests/test_health_endpoints.py`):
- HTTP endpoint functionality and response validation
- Telemetry data accuracy and formatting

**Shape Detection** (`tests/test_shape_detection.py`):
- Oracle Cloud instance shape detection and configuration

### Key Testing Features

- **Mocked Storage**: Tests use `MockMetricsStorage` to simulate database interactions
- **Time Control**: Tests use `patch('time.time')` for deterministic slot timing
- **Cache Management**: Tests properly handle P95 caching to ensure fresh data
- **State Isolation**: Each test starts with clean controller state
- **Algorithm Verification**: Tests validate the exact mathematical behavior of P95-driven control

## Future work

- **Smart network activation**: Trigger network load only when CPU/memory metrics trend below thresholds (predictive activation)
- **Additional Oracle shapes**: Support for newer compute shapes and specialized instances
- **Multi-cloud support**: Extend to AWS Free Tier, Google Cloud Free Tier
- **Advanced monitoring**: Integration with Oracle Cloud monitoring APIs for validation
- **Resource optimization**: Dynamic adjustment based on actual Oracle reclamation patterns


## Scheduling

The controller and CPU load workers run with low operating system priority using `os.nice(19)`.
On Linux/Unix systems this lowers their scheduling priority; on other platforms
the call is ignored. Tight loops include small `sleep` slices (≈5 ms) so the
scheduler can run other workloads without noticeable latency impact.

## Troubleshooting

### Verifying Load Generation

**Check CPU load is working:**
```shell
# CPU percentage should be near your target
docker logs -f loadshaper | grep "cpu now="
```

**Check memory allocation:**
```shell
# Memory usage should increase over time if MEM_TARGET_PCT > current usage
docker logs -f loadshaper | grep "mem(excl-cache)"
```

**Check network traffic:**
```shell
# Network percentage should show activity when NET_MODE=client and peers are configured
docker logs -f loadshaper | grep "nic("
```

### Common Issues

**CPU not reaching target percentage:**
- Check if `LOAD_THRESHOLD` is too low (workers pause when system load is high)
- Verify `CPU_STOP_PCT` isn't triggering premature shutdown
- Increase `CPU_P95_SETPOINT` if needed

**Memory not increasing:**
- Ensure sufficient free memory exists (respects `MEM_MIN_FREE_MB`)
- Check if `MEM_STOP_PCT` is being triggered
- Verify container has access to enough memory

**Network traffic not generating:**
- Confirm `NET_MODE=client` and `NET_PEERS` are set correctly
- Verify peers are running iperf3 servers on the specified port
- Check firewall rules between instances
- Try `NET_PROTOCOL=tcp` if UDP traffic is filtered

**Database storage issues:**
```shell
# Check if metrics database is working
docker exec loadshaper ls -la /var/lib/loadshaper/ 2>/dev/null || echo "Using fallback /tmp storage"
```

**Load average causing frequent pauses:**
```shell
# If workers pause too often, adjust thresholds
LOAD_THRESHOLD=1.0 LOAD_RESUME_THRESHOLD=0.6 docker compose up -d
```