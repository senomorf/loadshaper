# Technical Implementation Details

**Primary Instructions:** See [CLAUDE.md](CLAUDE.md) for AI agent guidelines and configuration.

This file contains supplementary technical details only when deeper implementation knowledge is needed.

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

## P95 CPU Controller Technical Algorithm

The **CPUP95Controller** implements advanced 95th percentile targeting using a state machine approach:

### State Machine Architecture
- **BUILDING**: Ramps up CPU intensity when P95 is below target
- **MAINTAINING**: Sustains optimal CPU load with controlled exceedances
- **REDUCING**: Backs off when P95 exceeds target or system load is high

### Exceedance Budget Management
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

## Network Fallback Controller Algorithm

The **NetworkFallbackState** provides intelligent network generation as a backup protection mechanism:

### Oracle Shape-Aware Logic
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

## Extended Testing Strategies

### Performance Benchmarks
```bash
# Measure loadshaper's own resource consumption
docker stats loadshaper --no-stream
# Expected: <1% CPU, 10-20MB memory when not actively generating load

# Test system responsiveness with loadshaper running
time ls -la /usr/bin/  # Should be <100ms
ping -c 5 8.8.8.8     # Should show normal latency

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

### Integration Test Workflow
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

## Configuration Templates

Oracle shape-specific configuration templates are available in `config-templates/`:
- `e2-1-micro.env`: E2.1.Micro (x86, 1/8 OCPU) - Conservative CPU, network-focused
- `e2-2-micro.env`: E2.2.Micro (x86, 1/4 OCPU) - Balanced CPU/network
- `a1-flex-1.env`: A1.Flex with 1 vCPU (ARM) - Memory occupation enabled
- `a1-flex-4.env`: A1.Flex with 4 vCPUs (ARM) - Full resource utilization

## Related Documentation
- [README.md](README.md) - Project overview, usage, and configuration
- [CONTRIBUTING.md](CONTRIBUTING.md) - Contributor setup and guidelines
- [CHANGELOG.md](CHANGELOG.md) - Version history and breaking changes
- [CLAUDE.md](CLAUDE.md) - Primary AI agent guidelines and instructions