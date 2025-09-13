# Changelog

All notable changes to `loadshaper` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**📖 Related Documentation:** [README.md](README.md) | [CONTRIBUTING.md](CONTRIBUTING.md) | [AGENTS.md](AGENTS.md)

## [Unreleased] - 2025-01-15

### ⚠️ BREAKING CHANGES - CRITICAL FIX
- **Persistent volume storage now REQUIRED** - Docker Compose deployments must include persistent volume or container will not start
- **Container now runs as non-root user** (uid/gid 1000) for security
- **No fallback to ephemeral storage** - LoadShaper requires persistent storage for Oracle compliance

### Fixed
- **CRITICAL**: Added persistent volume storage for metrics database in Docker Compose ([#74](https://github.com/senomorf/loadshaper/issues/74))
- **Metrics database persistence**: 7-day P95 history now preserved across container restarts
- **Oracle compliance**: P95 calculations maintain complete history required for reclamation detection
- **Container security**: Application now runs as non-root user (loadshaper:1000)

### Added
- **Entrypoint validation**: Container fails fast if persistent storage not properly mounted
- **Health endpoint enhancement**: Added `persistence_storage` status and `database_path` fields
- **Clear error messages**: Detailed guidance when persistent volume configuration is missing

### Changed
- **BREAKING**: Docker Compose now requires `loadshaper-metrics` named volume
- **BREAKING**: Container exits if `/var/lib/loadshaper` is not writable
- **BREAKING**: Removed all fallback logic to `/tmp` storage paths
- **Dockerfile**: Added non-root user setup and entrypoint script
- **Health checks**: Now validate persistence status explicitly

### Migration Required
Existing Docker Compose users must update their configuration:

```yaml
services:
  loadshaper:
    volumes:
      - loadshaper-metrics:/var/lib/loadshaper
      - ./config-templates:/app/config-templates:ro

volumes:
  loadshaper-metrics:
    driver: local
```

## [3.0.0] - Breaking Change Release

### ⚠️ BREAKING CHANGES
- **New P95-driven CPU control system** - replaces previous implementation completely
- **No backward compatibility** - this is intentional for the WIP project
- **Pure P95 control** - uses Oracle's exact 95th percentile measurement criteria

### Added
- **P95-driven CPU control**: Pure Oracle-compliant 95th percentile control system
- **CPU P95 state machine**: BUILDING/MAINTAINING/REDUCING states based on 7-day CPU P95 trends
- **Exceedance budget controller**: Maintains approximately 6.5% of time slots above threshold to achieve target P95
- **Proportional safety scaling**: Dynamic CPU intensity adjustment based on system load to maintain responsiveness while achieving P95 targets
- **Oracle rules compliance**: CPU uses P95 measurement, memory/network use simple thresholds (per official Oracle documentation)
- **P95 controller configuration**: `CPU_P95_TARGET_MIN`, `CPU_P95_TARGET_MAX`, `CPU_P95_SETPOINT`, etc.
- **Enhanced telemetry**: Shows CPU P95 controller state, exceedance percentage, and target ranges
- **Official Oracle documentation link**: Added to README and agent guidelines for reference
- **WIP project status**: Clear documentation that breaking changes are expected

### Changed
- **BREAKING**: CPU control logic completely replaced with pure P95 system
- **BREAKING**: All configuration files updated to use P95 variables only
- **BREAKING**: Helm charts updated with new P95 configuration structure
- **Telemetry format**: Removed P95 display for memory/network (Oracle doesn't use P95 for these metrics)
- **Health endpoints**: Include P95 controller status in JSON responses
- **Documentation accuracy**: Corrected Oracle reclamation rules across all documentation files

### Removed
- **CPU_TARGET_PCT variable**: Completely removed from codebase
- **Backward compatibility**: No support for old configuration format

### Fixed
- **Critical Oracle compliance issue**: CPU control now uses 95th percentile matching Oracle's exact reclamation criteria
- **Issue #73**: LoadShaper now uses P95 values for control decisions, not just telemetry display
- **Load gating mismatch**: Fixed critical bug where controller recorded high slots even when main loop forced baseline due to load constraints
- **Jumbo config template fixes**: Updated jumbo frame configuration templates to use correct P95 variables instead of deprecated CPU_TARGET_PCT
- **Test failures**: Resolved P95 cache pollution issues in test suite causing incorrect exceedance target calculations
- **Code documentation**: Added comprehensive docstrings and improved critical code comments for Oracle compliance logic

---

## [2.2.0] - Previous Version

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


### Development History

This project evolved from a simple CPU stress tool into a comprehensive Oracle Cloud Always Free resource manager. Key milestones:

1. **CPU-only generation** (v1.0) - Basic prevention of CPU-based reclamation
2. **Multi-resource support** (v1.1) - Added memory and network components  
3. **Oracle-compliant monitoring** (v1.2) - 7-day 95th percentile tracking
4. **Production-ready** (current) - Comprehensive documentation and testing

### Acknowledgments

Thanks to the Oracle Cloud Always Free community for feedback, testing, and feature requests that have shaped this tool's development.