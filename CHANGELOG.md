# Changelog

All notable changes to `loadshaper` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**📖 Related Documentation:** [README.md](README.md) | [CONTRIBUTING.md](CONTRIBUTING.md) | [AGENTS.md](AGENTS.md)

## [Unreleased]

### Added
- **Native Python network generator** (#71): Complete replacement of iperf3 with native socket-based implementation
  - RFC 2544 default addresses for serverless operation
  - Token bucket rate limiting with 5ms precision
  - IPv4/IPv6 support with TTL safety controls
  - Pre-allocated buffers for zero-copy packet generation
- **Health monitoring endpoints** (#18): HTTP server with /health and /metrics endpoints
  - Configurable via HEALTH_ENABLED, HEALTH_PORT, HEALTH_HOST
  - Docker health check integration ready
  - Security-first binding (defaults to localhost-only)
- **Graceful shutdown** (partial #12): Signal handling for SIGTERM/SIGINT with clean resource cleanup
- **Intelligent network fallback** (#26): Adaptive network generation based on Oracle reclamation rules
  - Shape-aware logic (E2 vs A1 different criteria)
  - Hysteresis and debounce to prevent oscillation
  - NET_ACTIVATION modes: adaptive, always, off
  - EMA-based rate adjustments for smoother behavior
- **MTU 9000 optimization**: Jumbo frame support with 30-50% CPU reduction
  - New *-jumbo.env configuration templates for all Oracle shapes
  - Optimized packet size (8900 bytes) for Oracle Cloud MTU 9000
  - UDP send buffer optimization (1MB)
- **Oracle shape auto-detection**: Automatically detects Oracle Cloud shapes (E2.1.Micro, E2.2.Micro, A1.Flex-1, A1.Flex-4)
- **Shape-specific configuration templates**: Pre-configured templates optimized for each Oracle shape
- **Template system**: ENV > TEMPLATE > DEFAULT priority for configuration management
- **Comprehensive test coverage**: New test suites for all features
  - test_network_timing.py, test_network_fallback.py
  - test_signal_handling.py, test_health_endpoints.py
  - test_oracle_validation.py, test_shape_detection_enhanced.py
- **Enhanced documentation**: Comprehensive overhaul with badges, FAQ, and configuration tables

### Changed
- **Memory calculation**: Modernized to industry-standard using MemAvailable
  - Aligned with AWS CloudWatch, Azure Monitor, Oracle standards
  - Simplified telemetry display (changed from `mem(no-cache)` to `mem(excl-cache)`)
  - Memory occupation terminology clarified ("occupation" vs "stressing")
- **Network control**: Replaced continuous PID with adaptive start/stop mechanism
  - EMA-based current utilization tracking instead of incorrect 95th percentile
  - Resource efficiency improvements to minimize CPU and memory overhead
- **Default packet size**: Increased to 8900 bytes for jumbo frame environments
- **Configuration**: Enhanced templates with jumbo frame variants and better defaults
- **Documentation**: Restructured README.md with improved organization and Quick Start section
- **Error handling**: Graceful handling of connection failures with exponential backoff

### Removed
- **iperf3 dependency**: Completely eliminated from codebase and Docker images
- **iperf3 service**: Removed from Docker Compose configuration
- **Legacy memory calculation**: Removed support for kernels without MemAvailable
- **DEBUG_MEM_METRICS**: Removed debug environment variable and dual metric display

### Fixed
- **Network fallback logic**: Corrected A1 shape logic (AND instead of OR for risk conditions)
- **Oracle criteria accuracy**: Fixed 95th percentile vs current utilization confusion
- **TCP buffer handling**: Fixed partial send issues in TCP mode
- **Token bucket dead zones**: Fixed issue where very low rates could prevent traffic generation
- **NET_PEERS validation**: Now accepts hostnames via DNS resolution fallback
- **TCP connection timeout**: Added 0.5s send timeout to prevent blocking
- **MTU validation**: Fixed large packet warnings and validation logic

### Breaking Changes
- **Linux 3.14+ required**: MemAvailable field dependency (older kernel support removed)
- **read_meminfo() signature**: Returns (total_bytes, used_pct, used_bytes) instead of 5-tuple
- **iperf3 removal**: External network server no longer supported
- **E2 configuration**: NET_TARGET_PCT raised from 15% to 25% to stay above Oracle's 20% threshold

### Technical Details
- Token bucket with 5ms ticks and elapsed-time based accumulation
- EMA smoothing for network utilization tracking with configurable alpha
- Signal handling for container orchestration compatibility
- Resource-aware fallback with minimum on/off periods to prevent oscillation
- New fallback control variables: NET_ACTIVATION, NET_FALLBACK_START_PCT, NET_FALLBACK_STOP_PCT
- Enhanced error handling prevents dead zones in rate limiting algorithm

### Security
- **Health endpoints security hardening**: Sanitized error messages, configurable host binding, HTTP method restrictions

## [1.2.0] - 2025-01-XX

### Added
- **7-day metrics storage**: SQLite database for rolling 7-day analysis
- **95th percentile calculations**: Mirrors Oracle's exact CPU reclamation criteria
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
- **Network traffic generation**: Initial implementation using iperf3 (replaced in v2.5.0)
- **Multi-platform support**: Both x86-64 (E2.1.Micro) and ARM64 (A1.Flex)
- **Docker Compose**: Complete deployment solution (iperf3 server removed in v2.5.0)
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