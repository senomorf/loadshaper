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

Currently `loadshaper` drives CPU usage and can emit network traffic, but it
does not yet monitor utilization over seven days or autonomously decide when to
apply network load.

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
falling below Oracle's thresholds. A future version could track recent metrics
and temporarily raise network usage until another metric is safely above the
limit or network usage reaches ~10 Mbps on E2 (or 0.2 Gbps per A1 vCPU).

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