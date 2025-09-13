# Changelog

All notable changes to `loadshaper` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**📖 Related Documentation:** [README.md](README.md) | [CONTRIBUTING.md](CONTRIBUTING.md) | [AGENTS.md](AGENTS.md)

## [Unreleased]

### Added
- **Oracle shape auto-detection**: Automatically detects Oracle Cloud shapes (E2.1.Micro, E2.2.Micro, A1.Flex-1, A1.Flex-4)
- **Shape-specific configuration templates**: Pre-configured templates optimized for each Oracle shape
- **Template system**: ENV > TEMPLATE > DEFAULT priority for configuration management
- **HTTP health check endpoints**: `/health` and `/metrics` endpoints for Docker health checks and monitoring systems
- **Configurable health server**: `HEALTH_ENABLED`, `HEALTH_PORT`, and `HEALTH_HOST` environment variables
- **Docker integration examples**: Health check configuration for docker-compose.yml and Dockerfile
- **Security-first binding**: Health server defaults to localhost-only (127.0.0.1) for security
- **Industry-standard memory calculation**: Uses MemAvailable (Linux 3.14+) with fallback for older kernels
- **Memory occupation improvements**: Configurable page touching frequency (`MEM_TOUCH_INTERVAL_SEC`)
- **Dual memory metrics**: Optional debug mode shows both cache-excluded and cache-included calculations
- **Memory calculation documentation**: Comprehensive explanation of why cache/buffers are excluded
- Comprehensive documentation overhaul with badges, FAQ, and configuration tables
- Architecture diagrams and component interaction documentation
- CONTRIBUTING.md with detailed contributor guidelines
- Enhanced troubleshooting section with concrete examples
- Performance benchmarks and continuous testing strategies

### Changed
- **Memory calculation method**: Upgraded to industry-standard approach aligned with AWS CloudWatch, Azure Monitor
- **Memory telemetry format**: Changed from `mem(no-cache)` to `mem(excl-cache)` for clarity
- **Memory occupation terminology**: Clarified "occupation" vs "stressing" throughout documentation
- Restructured README.md with improved organization and Quick Start section
- Enhanced AGENTS.md with external contributor guidelines and release process
- Updated GitHub repository description and topics for better discoverability

### Security
- **Health endpoints security hardening**: Sanitized error messages, configurable host binding, HTTP method restrictions

## [1.2.0] - 2025-01-XX

### Added
- **7-day metrics storage**: SQLite database for rolling 7-day analysis
- **95th percentile calculations**: Mirrors Oracle's exact reclamation criteria
- **Load average monitoring**: Automatic pausing when system under CPU contention
- **Hysteresis gap**: Prevents oscillation with thresholds (0.6 pause / 0.4 resume)
- **Metrics persistence**: Database survives container restarts
- **Telemetry enhancement**: Shows current, 5-minute average, and 95th percentile values

### Changed
- **Improved CPU responsiveness**: Better yielding to legitimate workloads
- **Enhanced safety systems**: More intelligent load average detection
- **Storage fallback**: Graceful handling of storage permission issues
- **Configuration flexibility**: More environment variables for fine-tuning

### Fixed
- **Memory leak prevention**: Proper cleanup of old metrics data
- **Thread safety**: Concurrent access to metrics database
- **Resource detection**: More accurate network interface speed detection

### Technical Details
- Database location: `/var/lib/loadshaper/metrics.db` (primary) or `/tmp/loadshaper_metrics.db` (fallback)
- Sample frequency: Every 5 seconds (≈120,960 samples per week)
- Database size: 10-20MB for 7 days of data
- Default load thresholds: 0.6 (pause) / 0.4 (resume)

## [1.1.0] - 2024-12-XX

### Added
- **Memory occupation**: Support for A1.Flex memory reclamation rules
- **Network traffic generation**: iperf3-based load generation as fallback
- **Multi-platform support**: Both x86-64 (E2.1.Micro) and ARM64 (A1.Flex)
- **Docker Compose**: Complete deployment solution with iperf3 server
- **Environment configuration**: Extensive customization via environment variables

### Changed
- **Priority optimization**: All workers run at nice 19 (lowest OS priority)
- **Transient bursts**: Short activity periods with frequent yielding
- **Safety thresholds**: Conservative defaults for Oracle Free Tier shapes

### Fixed
- **Network detection**: Better handling of container vs. host networking
- **Resource limits**: Proper respect for Oracle Free Tier constraints

## [1.0.0] - 2024-11-XX

### Added
- **Initial release**: Basic CPU load generation
- **Oracle shape detection**: Automatic E2.1.Micro and A1.Flex recognition
- **Safety mechanisms**: CPU and memory stop thresholds
- **Telemetry output**: Periodic logging of resource utilization
- **Docker support**: Container-based deployment
- **Low-priority operation**: Minimal impact on system responsiveness

### Features
- CPU utilization targeting with PID-style control
- Memory allocation for A1.Flex shapes
- Network utilization monitoring
- Configurable safety thresholds
- Unobtrusive background operation

---

## Version History Notes

### Oracle Free Tier Compatibility

**E2.1.Micro (x86-64):**
- 1/8 OCPU (burstable), 1GB RAM, 50 Mbps external
- Default targets: CPU 25%, Memory 0% (disabled), Network 15%
- Conservative settings for shared tenancy

**A1.Flex (ARM64):**
- Up to 4 OCPUs, 24GB RAM, 1 Gbps per vCPU  
- Default targets: CPU 35%, Memory 25%, Network 25%
- Higher targets for dedicated resources

### Breaking Changes

**v1.2.0:**
- Database schema introduction requires clean start for metrics
- New environment variables for load average control
- Changed default telemetry format to include 95th percentiles

**v1.1.0:**
- Environment variable naming standardization
- Docker Compose v3.9 requirement
- Network configuration restructure

### Migration Guide

**Upgrading to v1.2.0:**
```bash
# Clean restart recommended for new metrics database
docker compose down -v
docker compose up -d --build

# New environment variables (optional):
LOAD_THRESHOLD=0.6
LOAD_RESUME_THRESHOLD=0.4 
LOAD_CHECK_ENABLED=true
```

**Upgrading to v1.1.0:**
```bash  
# Update environment variable names:
# OLD: CPU_PERCENT -> NEW: CPU_TARGET_PCT
# OLD: MEM_PERCENT -> NEW: MEM_TARGET_PCT
# OLD: NET_PERCENT -> NEW: NET_TARGET_PCT

# Rebuild container:
docker compose up -d --build
```

### Development History

This project evolved from a simple CPU stress tool into a comprehensive Oracle Cloud Always Free resource manager. Key milestones:

1. **CPU-only generation** (v1.0) - Basic prevention of CPU-based reclamation
2. **Multi-resource support** (v1.1) - Added memory and network components  
3. **Oracle-compliant monitoring** (v1.2) - 7-day 95th percentile tracking
4. **Production-ready** (current) - Comprehensive documentation and testing

### Acknowledgments

Thanks to the Oracle Cloud Always Free community for feedback, testing, and feature requests that have shaped this tool's development.