# loadshaper

![License](https://img.shields.io/badge/License-MIT-blue.svg)
![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)
![Docker](https://img.shields.io/badge/Docker-supported-blue.svg)
![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20ARM64%20%7C%20x86--64-lightgrey.svg)
![Oracle Cloud](https://img.shields.io/badge/Oracle%20Cloud-Always%20Free-red.svg)

**Minimal baseline load generator for Oracle Cloud Always Free compute instances.**

## Problem

Oracle Cloud Always Free compute instances are automatically reclaimed if they remain underutilized for 7 consecutive days. An instance is considered idle when **ALL** of the following conditions are met over a 7-day window:

- 95th-percentile CPU utilization is below 20%
- Network utilization is below 20% of the shape's internet bandwidth cap  
- Memory utilization is below 20% (A1.Flex shapes only)

## Solution

`loadshaper` prevents VM reclamation by intelligently maintaining resource utilization above Oracle's thresholds while remaining completely unobtrusive to legitimate workloads. It:

✅ **Keeps at least one metric above 20%** to prevent reclamation  
✅ **Runs at lowest OS priority** (nice 19) with minimal system impact  
✅ **Automatically pauses** when real workloads need resources  
✅ **Tracks 95th percentile metrics** over 7-day rolling windows  
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

## How to run

On each VM:

```shell
mkdir -p loadshaper
# put the three files in place
docker compose up -d --build
# or: podman compose up -d
```

Then watch:

```shell
docker logs -f loadshaper
# or: podman logs -f loadshaper
```

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

`loadshaper` drives CPU usage and can emit network traffic. It now tracks CPU,
memory, and network utilization over a 7-day rolling window and calculates 95th
percentile values to mirror Oracle's reclamation criteria. The telemetry output
shows current, 5-minute average, and 7-day 95th percentile values for each metric.

## Architecture

`loadshaper` operates as a lightweight monitoring and control system with three main components:

### 1. **Metric Collection**
- **CPU utilization**: Read from `/proc/stat` (system-wide percentage)
- **Memory utilization**: Read from `/proc/meminfo` (excluding cache/buffers for A1.Flex shapes)  
- **Network utilization**: Read from `/proc/net/dev` with automatic speed detection
- **Load average**: Monitor from `/proc/loadavg` to detect CPU contention

### 2. **7-Day Metrics Storage**
- **SQLite database**: Stores samples every 5 seconds for rolling 7-day analysis
- **95th percentile calculation**: Mirrors Oracle's reclamation criteria exactly
- **Automatic cleanup**: Removes data older than 7 days
- **Storage locations**: `/var/lib/loadshaper/metrics.db` (preferred) or `/tmp/loadshaper_metrics.db` (fallback)

### 3. **Intelligent Load Generation**
- **CPU stress**: Low-priority workers (nice 19) with arithmetic operations
- **Memory allocation**: Gradual allocation with regular touching for A1.Flex shapes  
- **Network traffic**: iperf3-based bursts to peer instances when needed
- **Load balancing**: Automatic pausing when real workloads need resources

### Operation Flow
```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Collect       │───▶│   Analyze       │───▶│   Adjust        │
│   Metrics       │    │   95th %ile     │    │   Load Level    │
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

Network traffic should only be generated when CPU or memory activity risks
falling below Oracle's thresholds. With 7-day metrics tracking now in place,
future versions can make intelligent decisions about when to temporarily raise
network usage based on 95th percentile trends, ensuring at least one metric
stays above Oracle's reclamation thresholds.

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
[loadshaper] cpu now=45.2% avg=42.1% p95=48.3% | mem(no-cache) now=55.1% avg=52.8% p95=58.7% | nic(...) now=12.50% avg=11.25% p95=15.20% | load now=0.45 avg=0.42 p95=0.52 | ... | samples_7d=98547
```

Where:
- `now`: Current sample value
- `avg`: 5-minute exponential moving average
- `p95`: 95th percentile over the past 7 days
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
CPU_TARGET_PCT=50 CPU_STOP_PCT=70 MEM_TARGET_PCT=40 MEM_STOP_PCT=80 python -u loadshaper.py

# Configure load average monitoring thresholds (more aggressive example)
LOAD_THRESHOLD=1.0 LOAD_RESUME_THRESHOLD=0.6 LOAD_CHECK_ENABLED=true python -u loadshaper.py

# Conservative load monitoring (earlier pause, safer for shared systems)
LOAD_THRESHOLD=0.4 LOAD_RESUME_THRESHOLD=0.2 python -u loadshaper.py
```

## Configuration Reference

### Resource Targets

| Variable | Default | Description | E2.1.Micro | A1.Flex |
|----------|---------|-------------|------------|----------|
| `CPU_TARGET_PCT` | `25` | Target CPU utilization (%) | 25% (conservative) | 35% (more CPU available) |
| `MEM_TARGET_PCT` | `0` | Target memory utilization (%) | 0% (disabled) | 40% (memory rule applies) |
| `NET_TARGET_PCT` | `15` | Target network utilization (%) | 15% (50 Mbps limit) | 25% (1 Gbps per vCPU) |

### Safety Thresholds

| Variable | Default | Description | Notes |
|----------|---------|-------------|-------|
| `CPU_STOP_PCT` | `45` | CPU % to pause load generation | Conservative for shared tenancy |
| `MEM_STOP_PCT` | `80` | Memory % to pause allocation | Safe when MEM targeting disabled |
| `NET_STOP_PCT` | `40` | Network % to pause traffic | Conservative for external bandwidth |

### Control Behavior

| Variable | Default | Description |
|----------|---------|-------------|
| `CONTROL_PERIOD_SEC` | `5` | Seconds between control decisions |
| `AVG_WINDOW_SEC` | `300` | Exponential moving average window (5 min) |
| `HYSTERESIS_PCT` | `5` | Percentage hysteresis to prevent oscillation |
| `JITTER_PCT` | `15` | Random jitter in load generation (%) |
| `JITTER_PERIOD_SEC` | `5` | Seconds between jitter adjustments |

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

### Network Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `NET_MODE` | `client` | Network mode: `off`, `client` |
| `NET_PROTOCOL` | `udp` | Protocol: `udp` (lower CPU), `tcp` |
| `NET_PEERS` | `10.0.0.2,10.0.0.3` | Comma-separated peer IP addresses |
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
CPU_TARGET_PCT=25 MEM_TARGET_PCT=0 NET_TARGET_PCT=15
NET_LINK_MBIT=50 LOAD_THRESHOLD=0.6
```

**A1.Flex (ARM64):**
```bash  
# Higher targets for dedicated resources
CPU_TARGET_PCT=35 MEM_TARGET_PCT=40 NET_TARGET_PCT=25
NET_LINK_MBIT=1000 LOAD_THRESHOLD=0.8
```

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
A: Oracle reclaims instances when ALL metrics are below 20% for 7 days. `loadshaper` ensures at least one metric stays above 20% by tracking 95th percentiles just like Oracle does.

**Q: What if Oracle changes their reclamation policy?**  
A: The thresholds are easily configurable via environment variables. Simply adjust `CPU_TARGET_PCT`, `MEM_TARGET_PCT`, or `NET_TARGET_PCT` as needed.

**Q: Will Oracle consider this usage "legitimate"?**  
A: The tool generates actual resource utilization that would be visible to Oracle's monitoring. However, you should review Oracle's terms of service to ensure compliance with your use case.

### Technical Questions

**Q: Why does memory targeting default to 0% on E2.1.Micro?**  
A: E2 shapes only have 1GB RAM and memory isn't counted in Oracle's reclamation criteria for these instances. Memory targeting is only enabled by default on A1.Flex shapes.

**Q: How can I tell if it's working?**  
A: Watch the telemetry output: `docker logs -f loadshaper`. You'll see current, average, and 95th percentile values for all metrics.

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
docker logs -f loadshaper | grep "mem(no-cache)"
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
- Increase `CPU_TARGET_PCT` if needed

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