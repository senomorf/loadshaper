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

Currently `loadshaper` drives CPU usage and can emit network traffic, but it
does not yet monitor utilization over seven days or autonomously decide when to
apply network load.

## CPU load characteristics

CPU stress runs in a worker set to the lowest OS priority (`nice` 19).  The
workload uses short, jittered bursts so it stays transient and yields instantly
to real processes. When choosing a stress method, favor the lightest option that
meets the utilization requirement.

## Network shaping as fallback

Network traffic should only be generated when CPU or memory activity risks
falling below Oracle's thresholds. A future version could track recent metrics
and temporarily raise network usage until another metric is safely above the
limit or network usage reaches ~10 Mbps on E2 (or 0.2 Gbps per A1 vCPU).

## Overriding detection and thresholds

Environment variables can override shape detection and contention limits:

```shell
# Override detected NIC speed and adjust network caps
NET_SENSE_MODE=container NET_LINK_MBIT=10000 NET_STOP_PCT=20 python -u loadshaper.py

# Raise CPU target while lowering the safety stop
CPU_TARGET_PCT=50 CPU_STOP_PCT=70 MEM_TARGET_PCT=40 MEM_STOP_PCT=80 python -u loadshaper.py
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