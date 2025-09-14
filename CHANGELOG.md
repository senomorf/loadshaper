# Changelog

All notable changes to `loadshaper` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**📖 Related Documentation:** [README.md](README.md) | [CONTRIBUTING.md](CONTRIBUTING.md) | [AGENTS.md](AGENTS.md)

## [Unreleased] - 2025-01-15

### ⚠️ BREAKING CHANGES
- **Persistent volume storage now REQUIRED** - Docker Compose deployments must include persistent volume or container will not start
- **Network generation completely rewritten** - No backwards compatibility with previous implementation
  - Default NET_PEERS changed from placeholder IPs (10.0.0.2, 10.0.0.3) to public DNS servers (8.8.8.8, 1.1.1.1, 9.9.9.9)
  - Several new network configuration variables added for reliability and validation
- **Container security hardened** - Now runs as non-root user (uid/gid 1000) with no fallback to ephemeral storage

### Fixed
- **Critical network generation bug fixes**:
  - **State machine initialization**: Fixed debounce blocking first transition from OFF→INITIALIZING, preventing network generation startup
  - **CGNAT detection**: Fixed incomplete 100.64.0.0/10 range check (was only detecting 100.64.x.x, now properly detects entire 100.64.0.0-100.127.255.255)
  - **Special-use IP ranges**: Added missing RFC 2544 (198.18.0.0/15), TEST-NETs, and other reserved ranges to external address validation
  - **tx_bytes validation**: Fixed packet size mismatch in validation (now tracks actual bytes sent including DNS packet sizes)
  - **External egress verification**: Fixed false positives by checking actual peer used instead of any valid peer
- **Thread safety**: Ring buffer saves now use PID+thread temp files to prevent race conditions
- **Portable mount detection**: Replaced non-portable `stat -c %d` with Python-based device detection for Alpine/busybox compatibility
- **Configuration validation timing**: Moved runtime-dependent validations after system initialization to prevent startup errors
- **Network generation reliability**: Complete rewrite fixing silent failures and Oracle E2 compliance issues
  - Detects failed network generation via tx_bytes monitoring
  - Changed default peers from RFC2544 placeholder IPs to external addresses (8.8.8.8, 1.1.1.1, 9.9.9.9)
  - Validates external addresses rejecting RFC1918, loopback, and link-local for Oracle E2 compliance
  - Implements automatic protocol fallback: UDP → TCP → next peer
  - Added debounce and min-on/min-off time controls to prevent network state oscillation
- **Persistent volume storage**: Metrics database now properly persisted in Docker Compose
  - 7-day P95 history preserved across container restarts
  - P95 calculations maintain complete history required for Oracle reclamation detection
  - Container runs as non-root user (loadshaper:1000) for security
- **Database corruption**: Added detection and automatic recovery for SQLite corruption
- **Configuration validation**: Enhanced validation for CPU_P95_TARGET_MIN/MAX ordering and network fallback thresholds

### Added
- **NetworkGenerator state machine**: Complete state-driven network generation with reliability features
  - State progression: OFF → INITIALIZING → VALIDATING → ACTIVE_UDP → ACTIVE_TCP → ERROR
  - Peer validation and reputation with EMA-based scoring system
  - Runtime tx_bytes monitoring for actual traffic validation
  - Network health scoring (0-100) based on state, reputation, and errors
- **Persistent storage validation**: Enhanced container startup checks
  - Entrypoint validation ensures persistent storage is properly mounted
  - Health endpoint shows `persistence_storage` status
  - Clear error messages guide volume configuration
- **Performance optimizations**:
  - Ring buffer batching with configurable `CPU_P95_RING_BUFFER_BATCH_SIZE`
  - ENOSPC degraded mode for disk full scenarios
  - Thread-safe operations with PID+thread temp files
- **Monitoring enhancements**:
  - Memory usage tracking for P95 cache consumption
  - Database size monitoring with growth projections
  - Comprehensive configuration validation at startup
  - Automatic database corruption detection and recovery
- **Documentation improvements**:
  - Detailed P95 controller state machine documentation
  - Network fallback configuration examples
  - Migration guide for breaking changes

### Changed
- **Configuration templates**: All templates updated to use public DNS servers as default peers
- **Network configuration**: Default `NET_MIN_RATE_MBIT` changed from 0 to 1 Mbps to ensure minimum network activity
- **Health monitoring**: Health checks now validate persistence status explicitly
- **Network telemetry**: Enhanced to include state machine status, peer health, and validation metrics
- **Performance**: Ring buffer state saves batched to reduce I/O frequency (60s → 600s default)
- **Robustness**: Database corruption detection now runs on startup and during operations
- **Test patterns**: Updated for thread-safe temp file naming conventions

### Migration Required
Existing Docker Compose users must update their configuration:

**Persistent Storage (Required):**
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

**Network Configuration (Breaking Change):**
- **NET_PEERS default changed**: Old placeholder IPs (10.0.0.2, 10.0.0.3) now default to public DNS servers (8.8.8.8, 1.1.1.1, 9.9.9.9)
- **New network variables**: Validation, fallback, and reliability settings added
- **Configuration templates**: All shape-specific templates updated with new defaults
- **No backwards compatibility**: Old network implementation completely removed

## [3.0.0] - P95 CPU Control Implementation

### ⚠️ BREAKING CHANGES
- **New P95-driven CPU control system** - replaces previous implementation completely
- **No backward compatibility** - this is intentional for the WIP project
- **Pure P95 control** - uses Oracle's exact 95th percentile measurement criteria

### Added
- **P95-driven CPU control**: Pure Oracle-compliant 95th percentile control system
- **CPU P95 state machine**: BUILDING/MAINTAINING/REDUCING states based on 7-day CPU P95 trends
- **Exceedance budget controller**: Maintains approximately 6.5% of time slots above threshold to achieve target P95
- **Proportional safety scaling**: Dynamic CPU intensity adjustment based on system load to maintain responsiveness while achieving P95 targets
- **Thread safety**: Comprehensive RLock protection for all controller methods to prevent race conditions
- **Enhanced error recovery**: P95 controller gracefully handles temporary database failures using cached values
- **P95 controller configuration**: `CPU_P95_TARGET_MIN`, `CPU_P95_TARGET_MAX`, `CPU_P95_SETPOINT`, etc.
- **Enhanced telemetry**: Shows CPU P95 controller state, exceedance percentage, and target ranges
- **Improved test coverage**: 6 new test cases covering edge cases, configuration validation, and error handling
- **Official Oracle documentation link**: Added to README and agent guidelines for reference

### Changed
- **BREAKING**: CPU control logic completely replaced with pure P95 system
- **BREAKING**: All configuration files updated to use P95 variables only
- **BREAKING**: Helm charts updated with new P95 configuration structure
- **Telemetry format**: Removed P95 display for memory/network (Oracle doesn't use P95 for these metrics)
- **Health endpoints**: Include P95 controller status in JSON responses
- **Documentation accuracy**: Corrected Oracle reclamation rules across all documentation files
- **Logging improvements**: Enhanced configuration warnings and changed ring buffer messages to info level

### Fixed
- **Critical Oracle compliance issue**: CPU control now uses 95th percentile matching Oracle's exact reclamation criteria
- **LoadShaper now uses P95 values for control decisions, not just telemetry display**
- **Critical memory unpacking bug**: Fixed variable unpacking mismatch preventing runtime crashes
- **P95 cache fallback logic**: Fixed fallback to return cached values when database reads fail
- **Safety scaling efficiency**: Optimized method signatures to prevent redundant calculations
- **Thread safety gaps**: Added missing lock protection to all controller methods
- **Configuration validation**: Enhanced validation with clearer warning messages showing adjusted values
- **Load gating mismatch**: Fixed critical bug where controller recorded high slots even when forced to baseline
- **Test isolation issues**: Resolved shared state contamination in proportional scaling tests
- **JSON encoding bugs**: Fixed non-existent exception references in Python stdlib
- **Documentation corrections**: Fixed incorrect threshold descriptions and updated docstrings

### Removed
- **CPU_TARGET_PCT variable**: Completely removed from codebase
- **Backward compatibility**: No support for old configuration format

---

## [2.2.0] - Previous Version

### Added
- **Native Python network generator**: Complete replacement of iperf3 with native socket-based implementation
  - RFC 2544 default addresses for serverless operation
  - Token bucket rate limiting with 5ms precision
  - IPv4/IPv6 support with TTL safety controls
  - Pre-allocated buffers for zero-copy packet generation
- **Health monitoring endpoints**: HTTP server with /health and /metrics endpoints
  - Configurable via HEALTH_ENABLED, HEALTH_PORT, HEALTH_HOST
  - Docker health check integration ready
  - Security-first binding (defaults to localhost-only)
- **Graceful shutdown** (partial): Signal handling for SIGTERM/SIGINT with clean resource cleanup
- **Intelligent network fallback**: Adaptive network generation based on Oracle reclamation rules
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
- Database location: `/var/lib/loadshaper/metrics.db` (primary) or `/tmp/loadshaper_metrics.db` (fallback - removed in later versions)
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