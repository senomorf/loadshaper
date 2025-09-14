# loadshaper

![Oracle Cloud](https://img.shields.io/badge/Oracle%20Cloud-F80000?style=for-the-badge&logo=oracle&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-blue.svg)
![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)
![Docker](https://img.shields.io/badge/Docker-supported-blue.svg)
![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20ARM64%20%7C%20x86--64-lightgrey.svg)

## ⚠️ Work In Progress - Breaking Changes Expected

> **🚧 WORK IN PROGRESS: BREAKING CHANGES BY DESIGN**
>
> **Current Status:** Persistent storage is now **MANDATORY**. All fallback to `/tmp` has been completely removed.
> Containers will **NOT START** without proper persistent volumes. This is an intentional breaking change.
>
> **Development Philosophy:** Breaking changes are introduced frequently without migration paths **by design**.
> This approach prevents technical debt accumulation and enables rapid innovation toward the optimal
> Oracle Cloud VM protection solution.

### Migration Guide

**Latest Breaking Change: Mandatory Persistent Storage (Current Version)**

As of the current version, **persistent storage is mandatory**. The container will fail to start without proper volume configuration:

#### ✅ **Required Setup**
```yaml
# Docker Compose
volumes:
  - /var/lib/loadshaper:/var/lib/loadshaper

# Kubernetes
volumeMounts:
  - name: loadshaper-data
    mountPath: /var/lib/loadshaper
```

#### ❌ **What No Longer Works**
- Running without persistent volumes
- Fallback to `/tmp` storage (completely removed)
- Containers starting without writable `/var/lib/loadshaper`

#### 🔧 **Volume Permission Setup (User Responsibility)**

**LoadShaper requires proper volume permissions BEFORE starting** - no automatic fixes are provided for security reasons.

**Error**: "Cannot write to /var/lib/loadshaper - check volume permissions"

**REQUIRED: Pre-deployment Volume Setup**

For **Docker named volumes** (recommended):
```bash
# One-time setup: Create volume with correct permissions
docker run --rm -v loadshaper-metrics:/var/lib/loadshaper alpine:latest chown -R 1000:1000 /var/lib/loadshaper

# Then start LoadShaper
docker compose up -d
```

For **bind mounts**:
```bash
# Create and set permissions on host directory
sudo mkdir -p /var/lib/loadshaper
sudo chown -R 1000:1000 /var/lib/loadshaper
sudo chmod -R 755 /var/lib/loadshaper

# Update compose.yaml to use bind mount
volumes:
  - /var/lib/loadshaper:/var/lib/loadshaper
```

For **Kubernetes/OpenShift**:
```yaml
securityContext:
  runAsUser: 1000
  runAsGroup: 1000
  fsGroup: 1000  # Ensures volume has correct group ownership
```

**Verification**:
```bash
# Check container logs - should show success
docker logs loadshaper

# Verify volume ownership
docker run --rm -v loadshaper-metrics:/test alpine:latest ls -la /test
```

#### **Why This Change?**
- **Oracle Compliance**: 7-day P95 CPU calculations require persistent metrics database
- **Data Integrity**: Prevents silent failures that could cause VM reclamation
- **Performance**: Eliminates temporary storage overhead and reliability issues

**Next Breaking Changes:** Additional Oracle compliance improvements planned. Always check `CHANGELOG.md` before updating.

### Rootless Container Philosophy

LoadShaper follows **strict rootless container principles** for maximum security:

- **Never runs as root** - Container always executes as user `loadshaper` (UID/GID 1000)
- **No privilege escalation** - No automatic permission fixing or root operations
- **User responsibility** - Volume permissions must be configured correctly before deployment
- **Security first** - Prevents container breakout and follows container security best practices

**Why Rootless?**
- Eliminates container security vulnerabilities
- Follows least-privilege principle
- Compatible with security-conscious environments (Kubernetes, OpenShift)
- Prevents accidental host system modifications

**Modern native network generator implementation** - Uses Python sockets instead of external dependencies for maximum efficiency and control. Requires **Linux 3.14+ (March 2014)** with kernel MemAvailable support.

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

**📋 Prerequisites:**
- Docker and Docker Compose installed
- **Persistent storage required** - LoadShaper needs persistent volume for 7-day P95 metrics
- **Single instance only** - Run only one LoadShaper process per system to avoid conflicts
- **Rootless container setup** - LoadShaper follows security best practices (non-root user)

**1. Clone and setup:**
```bash
git clone https://github.com/senomorf/loadshaper.git
cd loadshaper
```

**2. REQUIRED: Setup volume permissions (one-time):**
```bash
# Create volume with correct permissions for rootless container
docker run --rm -v loadshaper-metrics:/var/lib/loadshaper alpine:latest chown -R 1000:1000 /var/lib/loadshaper
```

**3. Deploy:**
```bash
docker compose up -d --build
```

> **⚠️ Important**: LoadShaper follows rootless container security principles. Volume permissions MUST be configured correctly before starting the container - no automatic fixes are provided.

**4. Monitor activity:**
```bash
docker logs -f loadshaper
```

**3. See current metrics:**
```bash
# Look for telemetry lines showing current, average, and 95th percentile values
docker logs loadshaper | grep "\[loadshaper\]" | tail -5
```

That's it! `loadshaper` will automatically detect your Oracle Cloud shape and start maintaining appropriate resource utilization.

### Kubernetes/Helm Deployment

For Kubernetes deployments, Helm charts are available in the `helm/` directory:

```bash
# Install with default values
helm install loadshaper ./helm/loadshaper

# Or with custom configuration
helm install loadshaper ./helm/loadshaper -f custom-values.yaml
```

Key Kubernetes considerations:
- **Persistent Volume required** for 7-day P95 metrics storage
- **Resource limits included** - Default CPU/memory limits configured for Oracle Free Tier
- **Security hardened** - Read-only root filesystem and non-root user configured
- **Single replica only** - LoadShaper must not run multiple instances per node
- **Multiple configurations** - Production, security-hardened, and shape-specific value files included

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

```
┌─────────────────────────────────────────────────────────────────────┐
│                           LoadShaper                                │
├─────────────────┬─────────────────┬─────────────────┬───────────────┤
│  Metrics        │   P95 CPU       │  Load           │   Health      │
│  Collector      │   Controller    │  Generators     │   Server      │
│                 │                 │                 │               │
│ ┌─────────────┐ │ ┌─────────────┐ │ ┌─────────────┐ │ ┌───────────┐ │
│ │ /proc/stat  │ │ │ Ring Buffer │ │ │ CPU Workers │ │ │ /health   │ │
│ │ /proc/mem   │─┼─│ SQLite DB   │─┼─│ Mem Alloc   │ │ │ /metrics  │ │
│ │ /proc/net   │ │ │ State Mach  │ │ │ Net Traffic │ │ │ :8080     │ │
│ │ /loadavg    │ │ │ Slot Timing │ │ │             │ │ │           │ │
│ └─────────────┘ │ └─────────────┘ │ └─────────────┘ │ └───────────┘ │
└─────────────────┴─────────────────┴─────────────────┴───────────────┘
         │                   │                   │              │
         │                   │                   │              │
         ▼                   ▼                   ▼              ▼
    5s samples         7-day P95           Oracle VM        Docker/K8s
    EMA averages      calculations        protection        monitoring
```

`loadshaper` operates as a lightweight monitoring and control system with four main components:

### 1. **Metrics Collector**
- **CPU utilization**: Read from `/proc/stat` (system-wide percentage)
- **Memory utilization**: Read from `/proc/meminfo` using industry-standard calculation (see [Memory Calculation](#memory-calculation))
- **Network utilization**: Read from `/proc/net/dev` with automatic speed detection
- **Load average**: Monitor from `/proc/loadavg` to detect CPU contention

### 2. **P95 CPU Controller**
- **SQLite database**: Stores samples every 5 seconds for rolling 7-day analysis in persistent storage (`/var/lib/loadshaper/metrics.db`)
- **95th percentile calculation**: CPU only (mirrors Oracle's measurement method)
- **Automatic cleanup**: Removes data older than 7 days
- **Persistent storage requirement**: Database must be stored at `/var/lib/loadshaper/metrics.db` for 7-day history preservation

### 3. **Load Generators**
- **CPU stress**: Low-priority workers (nice 19) with arithmetic operations
- **Memory occupation**: Gradual allocation with periodic page touching for A1.Flex shapes  
- **Network traffic**: Native Python network bursts to peer instances when needed
- **Load balancing**: Automatic pausing when real workloads need resources

### 4. **Health Server**
- **HTTP endpoints**: `/health` and `/metrics` on port 8080 (configurable)
- **Docker integration**: Provides health checks for container orchestration
- **Monitoring support**: Real-time metrics for external monitoring systems
- **Security**: Binds to localhost by default, configurable for external access

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

## Intelligent Network Fallback

`loadshaper` implements smart network fallback to provide additional protection when CPU-based protection alone is insufficient. This feature generates network traffic only when Oracle's reclamation thresholds are at risk.

### How Network Fallback Works

**Activation Logic:**
- **E2 shapes**: Activates when CPU P95 AND network both approach Oracle's 20% threshold
- **A1 shapes**: Activates when CPU P95, network, AND memory all approach Oracle's 20% threshold

**Oracle Compliance:**
- Uses simple threshold monitoring for network (not P95) matching Oracle's measurement method
- Generates traffic only when needed as a fallback to CPU-based protection
- Smart debouncing prevents oscillation and reduces system impact

### Network Fallback Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `NET_ACTIVATION` | `adaptive` | Fallback mode: `adaptive`, `always`, `off` |
| `NET_FALLBACK_START_PCT` | `19.0` | Activate fallback below this network utilization threshold |
| `NET_FALLBACK_STOP_PCT` | `23.0` | Deactivate fallback above this network utilization threshold |
| `NET_FALLBACK_RISK_THRESHOLD_PCT` | `22.0` | CPU P95 and memory risk threshold for activation |
| `NET_FALLBACK_DEBOUNCE_SEC` | `30` | Minimum time between activation changes |
| `NET_FALLBACK_MIN_ON_SEC` | `60` | Minimum time to stay active once triggered |
| `NET_FALLBACK_MIN_OFF_SEC` | `30` | Minimum time to stay inactive once stopped |
| `NET_FALLBACK_RAMP_SEC` | `10` | Ramp-up time for gradual rate adjustment |

### Network Activation Modes

**`adaptive` (recommended):** Smart activation based on Oracle reclamation rules
- Monitors CPU P95, network, and memory utilization
- Activates only when multiple metrics approach danger thresholds
- Provides maximum protection with minimal system impact

**`always`:** Continuous network generation
- Useful for testing or environments with strict network requirements
- Higher resource usage but maximum network protection

**`off`:** Disables network fallback entirely
- CPU-only protection mode
- Recommended only when network generation is not desired

### Network Fallback Configuration Examples

#### 🔥 **Conservative Setup (Minimal Network Usage)**
*Activates network fallback only in extreme risk scenarios*
```bash
NET_ACTIVATION=adaptive
NET_FALLBACK_START_PCT=15.0          # Very low threshold
NET_FALLBACK_STOP_PCT=25.0           # Higher deactivation threshold
NET_FALLBACK_RISK_THRESHOLD_PCT=19.0 # More conservative risk level
NET_FALLBACK_DEBOUNCE_SEC=60         # Longer debounce to avoid rapid changes
NET_FALLBACK_MIN_ON_SEC=120          # Stay active longer once triggered
```
**Use case:** Environments where network activity should be minimized but Oracle compliance is critical.

#### ⚡ **Aggressive Setup (Maximum Oracle Compliance)**
*Maximizes protection against VM reclamation with active network generation*
```bash
NET_ACTIVATION=adaptive
NET_FALLBACK_START_PCT=22.0          # Higher activation threshold
NET_FALLBACK_STOP_PCT=28.0           # Higher deactivation threshold
NET_FALLBACK_RISK_THRESHOLD_PCT=24.0 # Proactive risk management
NET_FALLBACK_DEBOUNCE_SEC=15         # Quick response to changes
NET_FALLBACK_MIN_ON_SEC=60           # Standard minimum on time
```
**Use case:** Critical workloads where VM reclamation must be avoided at all costs.

#### 🧪 **Testing/Development Setup**
*Always-on network generation for testing network bandwidth and validation*
```bash
NET_ACTIVATION=always
NET_TARGET_PCT=25.0                  # Consistent 25% network utilization
NET_MODE=client                      # Client mode for outbound traffic
NET_PEERS=198.18.0.1,198.18.0.2    # RFC 2544 test addresses
```
**Use case:** Development environments, network performance testing, bandwidth validation.

#### 🚫 **CPU-Only Setup (No Network Generation)**
*Disables network fallback completely, relies only on CPU P95 control*
```bash
NET_ACTIVATION=off
NET_TARGET_PCT=0                     # No network generation
CPU_P95_TARGET_MIN=25.0             # Higher CPU target to compensate
CPU_P95_TARGET_MAX=30.0             # Adjusted range for CPU-only protection
```
**Use case:** Environments where network generation is prohibited or impossible.

#### 🏢 **Enterprise Setup (Balanced Protection)**
*Optimal balance between resource usage and Oracle compliance*
```bash
NET_ACTIVATION=adaptive
NET_FALLBACK_START_PCT=20.0          # Oracle threshold-based
NET_FALLBACK_STOP_PCT=25.0           # Safe deactivation level
NET_FALLBACK_RISK_THRESHOLD_PCT=22.0 # Standard risk threshold
NET_FALLBACK_DEBOUNCE_SEC=30         # Balanced response time
NET_FALLBACK_MIN_ON_SEC=60           # Standard minimum active period
NET_FALLBACK_RAMP_SEC=15            # Smooth transitions
```
**Use case:** Production environments requiring reliable Oracle compliance with reasonable resource usage.

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
- Required: `/var/lib/loadshaper/metrics.db` (persistent storage required)

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

## Local Development Setup

For local runs outside of Docker, you must first create the persistent storage directory:

```shell
# Create persistent storage directory with correct permissions
sudo mkdir -p /var/lib/loadshaper
sudo chown $USER:$USER /var/lib/loadshaper
```

**Note**: LoadShaper requires persistent storage at `/var/lib/loadshaper` to maintain the 7-day P95 CPU history needed for Oracle compliance. Without this directory, the application will fail to start.

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
>
> **🚨 CRITICAL: SINGLE INSTANCE ONLY:** Only run **ONE LoadShaper instance per system**. Multiple instances create race conditions in:
> - **P95 ring buffer state** - Concurrent writes corrupt slot history tracking
> - **Metrics database** - SQLite locks and data corruption
> - **Resource calculations** - Conflicting load measurements
>
> **Result:** Oracle VM reclamation due to broken P95 calculations. Each LoadShaper instance requires **exclusive access** to `/var/lib/loadshaper/` persistent storage.

### Resource Targets

| Variable | Auto-Configured Values | Description | E2.1.Micro | E2.2.Micro | A1.Flex-1 | A1.Flex-2 | A1.Flex-3 | A1.Flex-4 |
|----------|---------|-------------|------------|------------|------------|------------|------------|------------|
| `CPU_P95_SETPOINT` | **23.5**, 25.0, 25.0, 25.0, 25.0, 30.0 | Target CPU P95 (7-day window) | 23.5% | 25.0% | 25.0% | 25.0% | 25.0% | 30.0% |
| `MEM_TARGET_PCT` | **0**, 0, 30, 30, 30, 30 | Target memory utilization (%) | 0% (disabled) | 0% (disabled) | 30% (above 20% rule) | 30% (above 20% rule) | 30% (above 20% rule) | 30% (above 20% rule) |
| `NET_TARGET_PCT` | **15**, 25, 25, 25, 25, 30 | Target network utilization (%) | 15% (50 Mbps) | 25% (50 Mbps) | 25% (1 Gbps) | 25% (2 Gbps) | 25% (3 Gbps) | 30% (4 Gbps) |

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
| `JITTER_PCT` | `10` | Random jitter in load generation (%) |
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
| `CPU_P95_RING_BUFFER_BATCH_SIZE` | `10` | Number of slots between ring buffer state saves (performance optimization) |

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
| `NET_PORT` | `15201` | TCP port for network communication |
| `NET_BURST_SEC` | `10` | Duration of traffic bursts (seconds) |
| `NET_IDLE_SEC` | `10` | Idle time between bursts (seconds) |
| `NET_TTL` | `1` | IP TTL for generated packets |
| `NET_PACKET_SIZE` | `8900` | Packet size (bytes) for UDP/TCP generator |

### Network Interface Detection

| Variable | Default | Description |
|----------|---------|-------------|
| `NET_SENSE_MODE` | `container` | Detection mode: `container`, `host` |
| `NET_IFACE` | `ens3` | Host interface name (required when `NET_SENSE_MODE=host`) |
| `NET_IFACE_INNER` | `eth0` | Container interface name |
| `NET_LINK_MBIT` | `1000` | Fallback link speed (Mbps) |
| `NET_MIN_RATE_MBIT` | `1` | Minimum traffic generation rate |
| `NET_MAX_RATE_MBIT` | `800` | Maximum traffic generation rate |

### Proportional Safety Scaling

LoadShaper implements **proportional safety scaling** to prevent Oracle VM reclamation while maintaining system responsiveness. This advanced feature dynamically adjusts CPU intensity based on system load and P95 positioning.

#### Exceedance Budget Control

The P95 controller uses an "exceedance budget" approach:

| Variable | Default | Description |
|----------|---------|-------------|
| `CPU_P95_EXCEEDANCE_TARGET` | `6.5` | Target percentage of high-intensity slots (0-100%) |

**How it works:**
- **6.5% exceedance** means ~6.5% of configurable time slots (default 60s) run above the setpoint
- **93.5% of slots** run at or below the setpoint, achieving the target P95
- **Dynamic scaling**: High system load reduces intensity proportionally
- **Automatic adaptation**: Controller adjusts to maintain the exceedance budget

#### Load-Based Intensity Scaling

```python
# Example: At 80% system load, intensity scales down proportionally
if load_average > LOAD_THRESHOLD:
    intensity_factor = max(0.1, 1.0 - (load_average - LOAD_THRESHOLD) / 2.0)
    actual_intensity = base_intensity * intensity_factor
```

This ensures CPU stress never competes with legitimate workloads while maintaining Oracle compliance.

### Network Fallback Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `NET_ACTIVATION` | `adaptive` | Network mode: `adaptive` (fallback), `always`, `off` |
| `NET_FALLBACK_START_PCT` | `19` | Start network generation below this % |
| `NET_FALLBACK_STOP_PCT` | `23` | Stop network generation above this % |
| `NET_FALLBACK_RISK_THRESHOLD_PCT` | `22` | Oracle reclamation risk threshold for CPU/memory |
| `NET_FALLBACK_DEBOUNCE_SEC` | `30` | Seconds to wait before changing state |
| `NET_FALLBACK_MIN_ON_SEC` | `60` | Minimum seconds to stay active |
| `NET_FALLBACK_MIN_OFF_SEC` | `30` | Minimum seconds to stay inactive |
| `NET_FALLBACK_RAMP_SEC` | `10` | Rate ramp time for smooth transitions (seconds) |

### Network Performance Features

The native network generator provides advanced performance features:

**TCP Connection Pooling:**
- Persistent connections per target reduce connection overhead
- Automatic reconnection with exponential backoff on failures
- Significant performance improvement for sustained TCP traffic

**IPv6 Support:**
- Dual-stack networking with IPv4/IPv6 auto-detection
- Prefers IPv4 for compatibility, falls back to IPv6
- Proper TTL/hop limit handling for both address families

**DNS Resolution Caching:**
- Hostnames resolved once per session and cached
- Reduces DNS query overhead for repeated connections
- Supports both A and AAAA record resolution

**Protocol-Specific Optimizations:**
- UDP: Non-blocking sockets with optimized send buffers (1MB+)
- TCP: Connection pooling with TCP_NODELAY for low latency
- Jumbo frame support (MTU 9000) with 8900-byte packets for 30-50% CPU reduction

**Performance Considerations:**
- Rate limiting accuracy: 5ms token bucket precision
- Packet generation: Pre-allocated buffers for zero-copy operation
- Resource usage: Automatic cleanup with context manager support
- Thread safety: Designed for single-threaded operation per generator

### Shape-Specific Recommendations

**VM.Standard.E2.1.Micro (x86-64):**
```bash
# Conservative settings for shared 1/8 OCPU
CPU_P95_SETPOINT=23.5 CPU_P95_EXCEEDANCE_TARGET=6.5 MEM_TARGET_PCT=0 NET_TARGET_PCT=15
NET_LINK_MBIT=50 LOAD_THRESHOLD=0.6
```

**A1.Flex (ARM64):**
```bash
# Higher targets for dedicated resources
CPU_P95_SETPOINT=28.5 CPU_P95_EXCEEDANCE_TARGET=6.5 MEM_TARGET_PCT=25 NET_TARGET_PCT=25
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
  "persistence_storage": "available",
  "database_path": "/var/lib/loadshaper/metrics.db",
  "load_generation": "active",
  "storage_status": {
    "disk_usage_mb": 45.2,
    "oldest_sample": "2024-01-01T12:00:00Z",
    "sample_count": 120960
  }
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
    "cpu_p95_setpoint": 25.0,
    "memory_target": 0.0,
    "network_target": 15.0
  },
  "configuration": {
    "cpu_stop_threshold": 95.0,
    "memory_stop_threshold": 95.0,
    "network_stop_threshold": 95.0,
    "load_threshold": 0.6,
    "worker_count": 4,
    "control_period": 10.0,
    "averaging_window": 60.0
  },
  "percentiles_7d": {
    "cpu_p95": 48.3,
    "memory_p95": 52.1,
    "network_p95": 14.8,
    "load_p95": 0.35,
    "sample_count_7d": 120960
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

## Production Monitoring & Alerting

For production deployments, proper monitoring is essential to ensure LoadShaper effectively prevents Oracle VM reclamation while maintaining system stability.

### Key Metrics to Monitor

Monitor these critical metrics via the `GET /metrics` endpoint:

| Metric | Path | Critical Threshold | Description |
|--------|------|-------------------|-------------|
| **CPU P95** | `percentiles_7d.cpu_p95` | Must stay > 20% | Primary Oracle reclamation metric |
| **Health Status** | `status` | Must be "healthy" | Overall LoadShaper health |
| **Controller State** | `p95_controller.state` | Watch for stability | P95 controller operational state |
| **Exceedance %** | `p95_controller.exceedance_pct` | Target ~6.5% | Percentage of high-intensity slots |
| **Load Average** | `current.load_average` | Monitor spikes | System load impact tracking |

### Recommended Alert Thresholds

#### 🚨 CRITICAL Alerts
```bash
# LoadShaper Down - Immediate reclamation risk
curl -f http://localhost:8080/health || ALERT "LoadShaper unreachable"

# CPU P95 Below Oracle Threshold
if cpu_p95 < 20% for > 60 minutes:
  ALERT "CPU P95 approaching Oracle reclamation threshold"
```

#### ⚠️ WARNING Alerts
```bash
# Low CPU Activity Warning
if cpu_p95 < 25% for > 30 minutes:
  WARN "CPU P95 trending toward danger zone"

# High Exceedance - Overshooting target
if exceedance_pct > 15% for > 30 minutes:
  WARN "P95 controller exceedance above optimal range"

# Controller Instability
if p95_controller.state in ["BUILDING","REDUCING"] for > 20 minutes:
  WARN "P95 controller unable to reach stable state"
```

#### ℹ️ INFO Alerts
```bash
# Process Restart Tracking
if uptime_seconds < 60:
  INFO "LoadShaper restarted - monitoring for stability"
```

### Integration Examples

#### Prometheus + JSON Exporter
```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'loadshaper'
    static_configs:
      - targets: ['localhost:8080']
    metrics_path: /metrics
    scrape_interval: 30s
```

#### Simple Bash Monitoring Script
```bash
#!/bin/bash
# monitor-loadshaper.sh
METRICS=$(curl -s http://localhost:8080/metrics)
CPU_P95=$(echo $METRICS | jq -r '.percentiles_7d.cpu_p95 // 0')

if (( $(echo "$CPU_P95 < 20" | bc -l) )); then
  echo "CRITICAL: CPU P95 ($CPU_P95%) below Oracle threshold"
  # Send notification (email, Slack, PagerDuty, etc.)
fi
```

#### OCI Monitoring Validation
```bash
# Verify LoadShaper activity is visible to Oracle
# Check OCI Console > Compute > Instance Details > Metrics
# CPU Utilization should show consistent activity pattern
```

### Verification Checklist

**✅ Confirm LoadShaper is Working:**
1. CPU P95 stabilizes in target range (23-28%)
2. Controller state reaches "MAINTAINING" after warmup
3. Exceedance percentage near 6.5%
4. OCI Console shows consistent CPU activity

**⚠️ Warning Signs:**
- CPU P95 trending downward toward 20%
- Controller stuck in "BUILDING" or "REDUCING" states
- Volatile CPU patterns instead of stable control
- Gaps in monitoring data indicating downtime

**🔧 Troubleshooting:**
- Check Docker logs: `docker logs loadshaper`
- Verify database: `docker exec loadshaper ls -la /var/lib/loadshaper/`
- Test health endpoint: `curl http://localhost:8080/health`
- Monitor system load: Ensure load average isn't constantly high

### Monitoring Tool Recommendations

- **Prometheus + Grafana**: Best for comprehensive monitoring and alerting
- **Datadog/New Relic**: Good for managed environments with agent-based monitoring
- **Simple cron + curl**: Sufficient for basic deployments with shell script alerting
- **OCI Monitoring**: Use as secondary validation of LoadShaper effectiveness

**Remember**: The ultimate success metric is your VM remaining active. Monitor consistently, but trust the system's design to maintain Oracle compliance automatically.

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
A: Metrics history is preserved in the SQLite database (stored in `/var/lib/loadshaper/` on persistent storage). The 7-day rolling window continues from where it left off.

**Q: Can I run this alongside other applications?**  
A: Absolutely. That's the primary use case. `loadshaper` is designed to coexist peacefully with any workload.

### Troubleshooting

**Q: CPU load isn't reaching the target percentage**  
A: Check if `LOAD_THRESHOLD` is too low (causing frequent pauses) or if `CPU_STOP_PCT` is being triggered. Try increasing `LOAD_THRESHOLD` to 0.8 or 1.0.

**Q: Network traffic isn't being generated**  
A: Ensure you have `NET_MODE=client` and valid `NET_PEERS` IP addresses. Verify peer instances are reachable and firewall rules allow traffic on `NET_PORT`.

**Q: Memory usage isn't increasing on A1.Flex**
A: Check available free memory and ensure `MEM_TARGET_PCT` is set above current usage. Verify the container has adequate memory limits.

## Custom Persistent Storage Path

By default, LoadShaper uses `/var/lib/loadshaper` as its persistent storage directory. You can customize this location using the `PERSISTENCE_DIR` environment variable if needed.

### Docker Compose Override

To use a custom storage path with Docker Compose:

```yaml
# compose.override.yaml
services:
  loadshaper:
    environment:
      - PERSISTENCE_DIR=/data/loadshaper  # Custom path inside container
    volumes:
      - loadshaper-metrics:/data/loadshaper  # Mount volume to custom path
```

### Kubernetes ConfigMap

For Kubernetes deployments with custom paths:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: loadshaper-config
data:
  PERSISTENCE_DIR: "/data/loadshaper"
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: loadshaper-storage
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: loadshaper
spec:
  template:
    spec:
      containers:
      - name: loadshaper
        envFrom:
        - configMapRef:
            name: loadshaper-config
        volumeMounts:
        - name: storage
          mountPath: /data/loadshaper  # Must match PERSISTENCE_DIR
      volumes:
      - name: storage
        persistentVolumeClaim:
          claimName: loadshaper-storage
```

### Important Notes

- **Path consistency**: The `PERSISTENCE_DIR` environment variable must match the volume mount path
- **Volume permissions**: The directory must be owned by UID/GID 1000 (LoadShaper user)
- **Single instance**: Only one LoadShaper instance can use a given storage path
- **Migration**: When changing storage paths, historical metrics will not be migrated automatically

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

**Container startup failures:**
```shell
# Check container logs for entrypoint errors
docker logs loadshaper

# Common entrypoint issues:
# 1. "Permission denied" - persistent storage mount point permissions
docker exec loadshaper ls -ld /var/lib/loadshaper || echo "Mount point not accessible"
sudo chown -R 1000:1000 ./persistent-storage/  # Fix host permissions

# 2. "Write test failed" - storage not writable
docker exec loadshaper touch /var/lib/loadshaper/test && docker exec loadshaper rm /var/lib/loadshaper/test || echo "Storage not writable"

# 3. "Database migration failed" - corrupted or incompatible database
docker exec loadshaper rm -f /var/lib/loadshaper/metrics.db && docker restart loadshaper

# 4. Verify compose configuration includes persistent volume
docker compose config | grep -A5 volumes || echo "No volumes configured - add persistent storage"
```

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
- Verify peers are reachable on the specified port
- Check firewall rules between instances
- Try `NET_PROTOCOL=tcp` if UDP traffic is filtered

**Database storage issues:**
```shell
# Check if persistent metrics storage is working
docker exec loadshaper ls -la /var/lib/loadshaper/ 2>/dev/null || echo "Persistent storage not mounted - container will fail"

# If database corrupted, remove and restart (7-day P95 history will reset)
docker exec loadshaper rm -f /var/lib/loadshaper/metrics.db && docker restart loadshaper

# Check disk space and verify write permissions
docker exec loadshaper df -h /var/lib/loadshaper && docker exec loadshaper touch /var/lib/loadshaper/test && docker exec loadshaper rm /var/lib/loadshaper/test
```

**Load average causing frequent pauses:**
```shell
# If workers pause too often, adjust thresholds
LOAD_THRESHOLD=1.0 LOAD_RESUME_THRESHOLD=0.6 docker compose up -d
```

### Network Generator Troubleshooting

**Network connectivity issues:**
```shell
# Check firewall rules allow traffic on NET_PORT (default 15201)
# Verify NET_PEERS addresses are reachable
# For E2 shapes, ensure peers are external (not internal/localhost)
```

**DNS resolution failures:**
```shell
# Check DNS resolution for benchmark addresses
docker exec loadshaper nslookup 198.18.0.1
# Or use alternative benchmark address
NET_PEERS=203.0.113.1:15201 docker compose up -d --build
```

**Network generation issues:**
```shell
# Check container logs for network errors
docker logs loadshaper | grep -i network
# Look for peer connectivity issues
docker logs loadshaper | grep -i peer
```

**High network CPU usage:**
```shell
# Switch to UDP if TCP overhead is too high
NET_PROTOCOL=udp docker compose up -d --build
```

**Network interface detection problems:**
```shell
# Check available network interfaces
docker exec loadshaper cat /proc/net/dev
# Manual interface specification if auto-detection fails
NET_INTERFACE=eth0 docker compose up -d --build
```

### Performance Optimization

**For resource-constrained VMs (1 vCPU/1GB):**
```shell
# Optimize for minimal overhead with P95 control
CPU_P95_SETPOINT=22.0
MEM_TARGET_PCT=0
NET_TARGET_PCT=22
LOAD_THRESHOLD=0.4
docker compose up -d --build
```

**For better responsiveness:**
```shell
# More responsive to system load
CONTROL_PERIOD_SEC=1
AVG_WINDOW_SEC=5
HYSTERESIS_PCT=2
docker compose up -d --build
```

### Debug Information Collection

**Comprehensive system state:**
```shell
# Collect all relevant information for troubleshooting
echo "=== System Info ==="
docker exec loadshaper uname -a
docker exec loadshaper cat /proc/meminfo | head -5
docker exec loadshaper cat /proc/loadavg

echo "=== Loadshaper Config ==="
docker exec loadshaper env | grep -E "(CPU_|MEM_|NET_|LOAD_)" | sort

echo "=== Recent Logs ==="
docker logs --tail 50 loadshaper

echo "=== Network Connectivity ==="
docker exec loadshaper ping -c 3 198.18.0.1 2>/dev/null || echo "Benchmark address unreachable"

echo "=== Resource Usage ==="
docker stats --no-stream loadshaper
```

**Testing network generation manually:**
```shell
# Test native network generator directly
docker exec -it loadshaper python3 -c "
import sys
sys.path.append('/app')
import loadshaper

# Test UDP generation
gen = loadshaper.NetworkGenerator(rate_mbps=10, protocol='udp')
print('Starting UDP test...')
gen.start()
import time; time.sleep(5)
gen.stop()
print('UDP test complete')

# Test TCP generation
gen = loadshaper.NetworkGenerator(rate_mbps=10, protocol='tcp')
print('Starting TCP test...')
gen.start()
time.sleep(5)
gen.stop()
print('TCP test complete')
"
```

### Health Check Validation

**Verify health endpoints:**
```shell
# Test health endpoint if enabled
curl -f http://localhost:8080/health || echo "Health check failed"

# Test metrics endpoint
curl http://localhost:8080/metrics 2>/dev/null | head -10

# Check health server logs
docker logs loadshaper | grep -i health
```

### Oracle Reclamation Prevention Verification

**Check 7-day compliance:**
```shell
# Verify metrics meet Oracle thresholds
docker exec loadshaper python3 -c "
import sys, sqlite3, os
sys.path.append('/app')
import loadshaper

# Check database exists
db_path = '/var/lib/loadshaper/metrics.db'
if not os.path.exists(db_path):
    print('Persistent metrics database not found - volume not mounted correctly')
    exit(1)

# Check recent metrics
storage = loadshaper.MetricsStorage()
cpu_p95 = storage.get_percentile('cpu')
print(f'CPU 95th percentile: {cpu_p95:.1f}% (need >20%)' if cpu_p95 else 'CPU P95 not available')

try:
    print(f'Network samples available: {storage.get_sample_count()}')
    print('Check /metrics endpoint for current utilization levels')
except:
    print('Network metrics not available')

if loadshaper.is_e2_shape():
    print('E2 shape: CPU and network must be >20%')
else:
    try:
        print('Memory tracking active for A1 shapes')
        print('Check /metrics endpoint for current memory utilization')
    except:
        print('Memory metrics not available')
    print('A1 shape: CPU, network, AND memory must all be >20%')
"
```