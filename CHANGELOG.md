# Changelog

All notable changes to `loadshaper` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**📖 Related Documentation:** [README.md](README.md) | [CONTRIBUTING.md](CONTRIBUTING.md) | [AGENTS.md](AGENTS.md)

## [2.6.0] - 2025-01-15

### BREAKING CHANGES
- **Memory calculation modernization**: Removed backward compatibility for older kernels
- **Linux 3.14+ required**: Older kernel support (pre-March 2014) has been removed
- **Removed DEBUG_MEM_METRICS**: Debug mode for dual memory metrics is no longer available
- **Changed read_meminfo() signature**: Now returns (total_bytes, used_pct, used_bytes) instead of 5-tuple
- **Smart network fallback**: Network fallback now considers CPU p95 status to prevent unnecessary activation
- **CRITICAL FIX: A1 fallback logic**: Fixed incorrect OR logic that caused unnecessary network traffic - now correctly uses AND logic (activates only when ALL metrics at risk)
- **E2 configuration fix**: NET_TARGET_PCT raised from 15% to 25% to stay above Oracle's 20% threshold
- **WIP project disclaimer**: Added prominent warning that project intentionally breaks backward compatibility

### Removed
- Fallback memory calculation for kernels without MemAvailable field
- DEBUG_MEM_METRICS environment variable and dual metric display
- References to older kernel support in documentation

### Changed
- **Simplified memory calculation**: Only uses MemAvailable field (industry standard)
- **Cleaner telemetry**: Memory display no longer shows debug metrics
- **Updated documentation**: Removed all references to fallback methods and debug options

## [2.5.2] - 2025-01-15

### Added
- **Adaptive network fallback mechanism**: Network generation now activates only when needed to prevent Oracle VM reclamation
- **NET_ACTIVATION mode control**: Configure network behavior as `adaptive` (default), `always`, or `off`
- **Hysteresis and debounce**: Prevents oscillation with configurable start/stop thresholds and minimum on/off times
- **Gradual rate ramping**: EMA-based rate adjustments replace aggressive PID control for smoother network behavior
- **Cooperative VM traffic awareness**: Network fallback accounts for traffic from other VMs on the same host

### Changed
- **Corrected Oracle idle criteria documentation**: CPU uses 95th percentile, network/memory use current utilization only
- **Network telemetry format**: Now shows EMA-based current utilization instead of incorrect 95th percentile
- **Resource efficiency**: Network generation minimized to reduce CPU and memory overhead
- **Control algorithm**: Replaced continuous PID control with adaptive start/stop mechanism

### Fixed
- **Oracle criteria accuracy**: Removed incorrect 95th percentile assumptions for network and memory metrics
- **PID controller integration**: Fixed network rate control when using default RFC 2544 peers
- **NET_PEERS validation**: Now accepts hostnames via DNS resolution fallback
- **TCP connection timeout**: Added 0.5s send timeout after connection to prevent blocking
- **Large packet warnings**: Fixed MTU validation and warning logic
- **Configuration defaults**: Added proper defaults for NET_TTL, NET_PACKET_SIZE, and rate limits

### Technical Details
- New fallback control variables: `NET_ACTIVATION`, `NET_FALLBACK_START_PCT`, `NET_FALLBACK_STOP_PCT`, `NET_DEBOUNCE_SEC`, `NET_MIN_ON_SEC`, `NET_MIN_OFF_SEC`, `NET_RATE_STEP_MBIT`
- Oracle compliance: Only CPU requires 95th percentile < 20%; network and memory use current utilization < 20%
- EMA smoothing with configurable alpha for responsive yet stable current utilization tracking
- Minimum activation periods prevent rapid on/off cycling that would waste resources

## [2.5.1] - 2025-01-15

### Added
- **MTU 9000 (Jumbo Frames) optimization**: Default `NET_PACKET_SIZE=8900` optimized for Oracle Cloud MTU 9000 environments
- **Jumbo frame configuration templates**: New `*-jumbo.env` templates for all Oracle shapes with MTU 9000 optimization
- **UDP send buffer optimization**: 1MB send buffer for improved UDP performance
- **Enhanced documentation**: MTU 9000 optimization guide and configuration examples

### Fixed
- **TCP buffer advancement bug**: Fixed partial send handling in TCP mode that could cause incomplete data transmission
- **Token bucket dead zone**: Fixed issue where very low rates or large packets could prevent any traffic generation
- **Buffer management**: Improved socket buffer settings for both UDP and TCP protocols

### Changed
- **Default packet size**: Increased from 1200 to 8900 bytes for 30-50% CPU reduction with jumbo frames
- **Performance optimization**: Better rate control with larger packets and improved socket buffering
- **Template coverage**: Extended configuration templates to support both standard and jumbo frame environments

### Technical Details
- MTU 9000 allows UDP payload up to 8972 bytes and TCP MSS up to 8960 bytes
- Token bucket now ensures minimum capacity of one packet regardless of rate/size combination  
- Enhanced error handling prevents dead zones in rate limiting algorithm
- Backward compatibility maintained via `NET_PACKET_SIZE` configuration override

## [2.5.0] - 2025-01-15

### Added
- **Native Python network generator**: Replaced iperf3 with native Python socket-based traffic generation
- **RFC 2544 default addresses**: Safe default target addresses (198.18.0.1, 198.19.255.254) when NET_PEERS is empty
- **TTL safety configuration**: `NET_TTL` variable (default 1) ensures packets only reach first hop
- **Configurable packet size**: `NET_PACKET_SIZE` variable for customizable UDP payload size
- **Token bucket rate limiting**: Precise 5ms tick intervals with elapsed-time based accumulation
- **Drift-free timing**: Monotonic clock-based timing prevents cumulative drift
- **IPv4/IPv6 support**: Automatic address family detection with proper TTL/hop-limit configuration

### Changed
- **Eliminated external dependencies**: Removed iperf3 dependency from Docker image and code
- **Improved rate control**: Native implementation provides better precision than subprocess calls
- **Enhanced safety**: TTL=1 default prevents accidental network impact beyond first hop
- **Protocol behavior**: TCP mode requires explicit NET_PEERS to avoid timeouts on RFC 2544 addresses
- **Error handling**: Graceful handling of connection failures with exponential backoff

### Removed
- **iperf3 dependency**: Completely removed from Dockerfile, compose.yaml, and codebase
- **iperf3 service**: Removed from Docker Compose configuration

### Technical Details
- Token bucket with 5ms ticks and 2x tick-size burst cap
- Pre-allocated memoryview buffers for zero-copy packet generation
- Automatic IPv4/IPv6 detection with proper TTL/hop-limit settings
- Connection pooling with per-burst socket creation for reliability
- Integration with existing PID controller system maintains all control logic

## [Unreleased]

### Added
- **Oracle shape auto-detection**: Automatically detects Oracle Cloud shapes (E2.1.Micro, E2.2.Micro, A1.Flex-1, A1.Flex-4)
- **Shape-specific configuration templates**: Pre-configured templates optimized for each Oracle shape
- **Template system**: ENV > TEMPLATE > DEFAULT priority for configuration management
- **HTTP health check endpoints**: `/health` and `/metrics` endpoints for Docker health checks and monitoring systems
- **Configurable health server**: `HEALTH_ENABLED`, `HEALTH_PORT`, and `HEALTH_HOST` environment variables
- **Docker integration examples**: Health check configuration for docker-compose.yml and Dockerfile
- **Security-first binding**: Health server defaults to localhost-only (127.0.0.1) for security
- **Industry-standard memory calculation**: Uses MemAvailable (Linux 3.14+) 
- **Memory occupation improvements**: Configurable page touching frequency (`MEM_TOUCH_INTERVAL_SEC`)
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