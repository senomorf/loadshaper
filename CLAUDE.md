# Claude Agent Guidelines

- Follow `README.md` for Oracle Free Tier thresholds: E2 shapes cap at 50 Mbps (≈10 Mbps threshold); A1.Flex offers 1 Gbps per vCPU (≈0.2 Gbps threshold) and is the only shape subject to the 20 % memory rule.
- CPU stress must run at `nice` 19, use transient bursts, and yield immediately to real workloads.
- Generate network traffic only as a fallback when CPU or memory activity risks dropping below thresholds.
- **Critical**: CPU load must have minimal impact on system responsiveness - always choose the lightest workload type that minimizes latency for other processes.
- Key overrides: `NET_SENSE_MODE`, `NET_LINK_MBIT`, `CPU_TARGET_PCT`, `NET_STOP_PCT`, `MEM_STOP_PCT`, `LOAD_THRESHOLD`.
- Run `python -m pytest -q` and keep docs (`README.md`, `AGENTS.md`, this file) in sync.
- Code style: Python 3, PEP 8, 4‑space indentation, minimal dependencies (stdlib + `iperf3`).
