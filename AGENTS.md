# Repository Guidelines

## Project Structure & Module Organization
- `loadshaper.py` — single-process controller that shapes CPU, RAM, and NIC load; reads config from environment; prints periodic telemetry.
- `Dockerfile` — Python 3 Alpine image with `iperf3`; runs `loadshaper.py`.
- `compose.yaml` — two services: `loadshaper` (client/loader) and `iperf3` (receiver) with configurable env vars.
- `README.md`, `LICENSE` — usage and licensing.

## Build, Test, and Development Commands
- Build & run in Docker: `docker compose up -d --build`
- Tail logs: `docker logs -f loadshaper`
- Local run (Linux only, needs /proc): `python -u loadshaper.py`
- Override settings at launch, e.g.: `CPU_TARGET_PCT=35 NET_PEERS=10.0.0.2,10.0.0.3 docker compose up -d`

## Coding Style & Naming Conventions
- Language: Python 3; 4‑space indentation; PEP 8 style.
- Names: functions/variables `snake_case`; constants `UPPER_SNAKE_CASE` (matches existing env-backed config).
- Keep dependencies minimal (standard library + `iperf3` binary). Avoid adding Python deps unless essential.
- Prefer small, testable helpers; keep I/O at edges; maintain clear separation between sensing, control, and workers.

## Testing Guidelines
- No formal test suite yet. Validate behavior by running the stack and observing `[loadshaper]` telemetry.
- CPU/RAM only: `NET_MODE=off docker compose up -d`.
- Network shaping: set peers (comma-separated IPs) via `NET_PEERS` and ensure peers run an iperf3 server on `NET_PORT`.
- Safety checks: verify `*_STOP_PCT` thresholds trigger pause/resume; include log snippets in PRs.

## Commit & Pull Request Guidelines
- Commits: clear, imperative subject lines; mention touched subsystems (cpu, mem, net, compose, docs) and key env vars.
- PRs must include: summary, rationale, config/env changes, manual test steps, and before/after telemetry screenshots or log lines.
- Update `README.md` for user-facing changes (flags, env vars, run instructions).

## Security & Configuration Tips
- Host NIC sensing (`NET_SENSE_MODE=host`) expects `/sys/class/net` bind-mounted to `/host_sys_class_net`; otherwise use `container` mode with `NET_LINK_MBIT`.
- Use unprivileged `NET_PORT` (default 15201). Be cautious raising `MEM_STOP_PCT` or `NET_MAX_RATE_MBIT` on shared hosts.

