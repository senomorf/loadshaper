# Changelog

All notable changes to `loadshaper` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**📖 Related Documentation:** [README.md](README.md) | [CONTRIBUTING.md](CONTRIBUTING.md) | [AGENTS.md](AGENTS.md)

## [3.0.0] - Breaking Change Release

### ⚠️ BREAKING CHANGES
- **Removed CPU_TARGET_PCT completely** - use `CPU_P95_*` variables instead
- **No migration path provided** - manually update configurations
- **Complete replacement of CPU control system** - pure P95-driven control only

### Added
- **P95-driven CPU control**: Pure Oracle-compliant 95th percentile control system
- **CPU P95 state machine**: BUILDING/MAINTAINING/REDUCING states based on 7-day CPU P95 trends
- **Exceedance budget controller**: Maintains approximately 6.5% of time slots above threshold to achieve target P95
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
- **Test failures**: Resolved P95 cache pollution issues in test suite causing incorrect exceedance target calculations
- **Code documentation**: Added comprehensive docstrings and improved critical code comments for Oracle compliance logic

---

## [2.2.0] - Previous Version

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


### Development History

This project evolved from a simple CPU stress tool into a comprehensive Oracle Cloud Always Free resource manager. Key milestones:

1. **CPU-only generation** (v1.0) - Basic prevention of CPU-based reclamation
2. **Multi-resource support** (v1.1) - Added memory and network components  
3. **Oracle-compliant monitoring** (v1.2) - 7-day 95th percentile tracking
4. **Production-ready** (current) - Comprehensive documentation and testing

### Acknowledgments

Thanks to the Oracle Cloud Always Free community for feedback, testing, and feature requests that have shaped this tool's development.