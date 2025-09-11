# loadshaper

Minimal baseline load generator for Oracle Cloud Always Free compute instances.
Idle instances may be reclaimed if, over a 7‑day window, the following are all
true:

- 95th‑percentile CPU utilization is below 20 %
- Network utilization is below 20 % of the shape's internet cap
- Memory utilization is below 20 % (A1.Flex shapes only)

`loadshaper` strives to keep at least one metric above the threshold while
remaining as unobtrusive as possible.

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

The VM.Standard.E2.1.Micro shape exposes up to 50 Mbps to the internet, so the
20 % network criterion equates to roughly 10 Mbps. A1.Flex shapes provide
1 Gbps per vCPU; apply the same 20 % rule, e.g. a single vCPU must sustain about
0.2 Gbps. Memory reclamation checks apply only to A1.Flex shapes.

`loadshaper` drives CPU usage and can emit network traffic. It now tracks CPU,
memory, and network utilization over a 7-day rolling window and calculates 95th
percentile values to mirror Oracle's reclamation criteria. The telemetry output
shows current, 5-minute average, and 7-day 95th percentile values for each metric.

## CPU load characteristics

CPU stress runs in a worker set to the lowest OS priority (`nice` 19).  The
workload uses short, jittered bursts so it stays transient and yields instantly
to real processes. When choosing a stress method, favor the lightest option that
meets the utilization requirement.

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

## Future work

- Record rolling seven‑day CPU, memory, and network metrics to mirror Oracle's
  reclamation checks.
- Trigger network load only when other metrics remain below thresholds.
- Implement memory stressors for A1.Flex shapes.


## Scheduling

The controller and CPU load workers run with low operating system priority using `os.nice(19)`.
On Linux/Unix systems this lowers their scheduling priority; on other platforms
the call is ignored. Tight loops include small `sleep` slices (≈5 ms) so the
scheduler can run other workloads without noticeable latency impact.