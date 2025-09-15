# Contributing to loadshaper

Thank you for your interest in contributing to `loadshaper`! This project helps Oracle Cloud Always Free users prevent VM reclamation through intelligent resource management.

## ⚠️ Project Status: Work In Progress

**This project intentionally breaks backward compatibility without providing migration paths.** This is a design choice that enables rapid iteration and prevents technical debt accumulation. Contributors should expect:

- **Breaking changes** in every release
- **No migration guides** or backward compatibility layers
- **Rapid evolution** of APIs, configuration, and architecture
- **Fresh implementations** that replace previous approaches entirely

This approach allows us to quickly iterate toward optimal Oracle Cloud VM protection.

**📖 Quick Links:**
- [README.md](README.md) - Project overview and usage instructions
- [AGENTS.md](AGENTS.md) - Development guidelines and testing strategies
- [CHANGELOG.md](CHANGELOG.md) - Version history and breaking changes

## Quick Start for Contributors

### Development Environment Setup

1. **Fork and clone the repository:**
   ```bash
   git clone https://github.com/senomorf/loadshaper.git
   cd loadshaper
   ```

2. **Set up Python virtual environment:**
   ```bash
   # Required: use venv for Python/pip commands
   python3 -m venv venv
   source venv/bin/activate  # Linux/Mac
   # venv\Scripts\activate   # Windows

   # Install development dependencies
   pip install -r requirements-dev.txt
   ```

3. **Test the setup:**
   ```bash
   # Run unit tests (pytest is the standard testing framework)
   pytest -q
   
   # Test Docker build
   docker compose build
   
   # Create persistent storage directory (MANDATORY - no fallback to /tmp)
   mkdir -p /var/lib/loadshaper
   # For local development, use your user
   sudo chown $USER:$USER /var/lib/loadshaper
   # For Docker, match container UID/GID
   # sudo chown 1000:1000 /var/lib/loadshaper

   # Test runtime (requires Linux)
   python3 -u loadshaper.py  # or use Docker
   ```

## Types of Contributions Welcome

### 🐛 Bug Fixes
- Resource monitoring inaccuracies
- Load generation not reaching targets
- Safety system failures
- Platform compatibility issues

### ✨ Feature Enhancements
- **New Oracle shapes**: Support for additional compute shapes
- **Other cloud providers**: AWS, GCP, Azure equivalents
- **Monitoring improvements**: Better metrics, alerting
- **Performance optimizations**: Lower overhead, smarter algorithms

### 📖 Documentation
- README improvements (see [README.md](README.md))
- Configuration examples
- Troubleshooting guides
- API documentation

### 🧪 Testing
- Additional test cases (see [AGENTS.md](AGENTS.md) for comprehensive testing guidelines including network generator tests)
- Platform-specific tests
- Performance benchmarks
- Edge case coverage

## Development Guidelines

### Security and Rootless Container Requirements

LoadShaper follows **strict rootless container principles** for maximum security:

1. **Never run as root**: The container runs as UID/GID 1000 (non-root user)
2. **No privilege escalation**: Never add `privileged: true` or `CAP_ADD` capabilities
3. **User responsibility**: Volume permissions are the user's responsibility
4. **Fail-fast validation**: Container exits immediately if persistent storage is not writable

**Volume Permission Setup:**
```bash
# For bind mounts - set permissions before starting container
mkdir -p /var/lib/loadshaper
chown 1000:1000 /var/lib/loadshaper  # Match container's UID/GID

# For named volumes - requires one-time permission setup
docker volume create loadshaper-metrics
docker run --rm -v loadshaper-metrics:/var/lib/loadshaper alpine:latest chown -R 1000:1000 /var/lib/loadshaper

# For custom paths - use PERSISTENCE_DIR environment variable
export PERSISTENCE_DIR=/custom/path/to/storage
mkdir -p "$PERSISTENCE_DIR"
chown 1000:1000 "$PERSISTENCE_DIR"
```

**Testing Rootless Compliance:**
```bash
# Verify container runs as non-root
docker run --rm loadshaper:latest id
# Should output: uid=1000(loadshaper) gid=1000(loadshaper)

# Test with read-only volume (should fail fast)
docker run --rm -v /tmp/readonly:/var/lib/loadshaper:ro loadshaper:latest
# Should exit with clear error about permissions
```

### Code Style
- **Language**: Python 3.8+
- **Formatting**: PEP 8 style, 4-space indentation
- **Dependencies**: Keep minimal (stdlib only)
- **Names**: `snake_case` for functions/variables, `UPPER_SNAKE_CASE` for constants
- **Priority**: All CPU work must use `os.nice(19)` for lowest priority

### Testing Requirements

**Before submitting any PR:**

1. **Unit tests must pass:**
   ```bash
   python -m pytest -q
   ```

2. **Manual testing:**
   ```bash
   # Test basic functionality
   docker compose up -d --build
   docker logs -f loadshaper

   # Look for telemetry output showing metrics
   # Verify CPU/memory/network targets are being reached

   # Test network generator specifically
   # Check for network traffic in telemetry logs
   # Verify adaptive fallback behavior with NET_ACTIVATION=adaptive

   # Test different protocols
   NET_PROTOCOL=tcp docker compose up -d --build
   NET_PROTOCOL=udp docker compose up -d --build

   # Test custom targets (optional)
   NET_PEERS=192.0.2.1 NET_PROTOCOL=udp docker compose up -d --build
   ```

**P95 CPU Controller Testing:**
   ```bash
   # Test P95 controller state machine and exceedance budget
   docker compose up -d --build
   docker logs -f loadshaper | grep "P95"

   # Look for controller state changes (BUILDING/MAINTAINING/REDUCING)
   # Verify exceedance percentage near target (6.5%)
   # Check CPU P95 stays above 20% (Oracle compliance)

   # Query P95 directly from database (requires direct container access)
   docker exec -it loadshaper python3 -c "
   import sys; sys.path.append('/app')
   import loadshaper
   storage = loadshaper.MetricsStorage()
   p95 = storage.get_percentile('cpu', 95)
   print(f'Current CPU P95: {p95:.1f}%')
   print('Status: OK' if p95 and p95 > 20 else 'RISK: Below Oracle 20% threshold')
   "

   # Test different P95 targets
   CPU_P95_SETPOINT=30.0 docker compose up -d --build
   CPU_P95_EXCEEDANCE_TARGET=10.0 docker compose up -d --build
   ```

3. **Safety verification:**
   ```bash
   # Test that load workers pause under system stress
   LOAD_THRESHOLD=0.1 docker compose up -d
   # Verify workers pause quickly
   
   # Test responsiveness
   time ls -la /usr/bin/  # Should be <100ms
   ```

## Contribution Process

### 1. Planning
- **Check existing issues** to avoid duplicate work
- **Open an issue** for discussion of major changes
- **Fork the repository** and create a feature branch

### 2. Implementation
- **Follow code style** guidelines above
- **Add tests** for new functionality
- **Update documentation** for user-facing changes
- **Keep changes focused** - one feature/fix per PR

### 3. Testing
- **Run all tests** locally before pushing
- **Test on actual Oracle Cloud** instances when possible
- **Verify both E2.1.Micro and A1.Flex** compatibility
- **Document any manual testing steps**

### 4. Submission
- **Create descriptive PR** with:
  - Clear summary of changes
  - Rationale for the change
  - Any new environment variables
  - Manual testing steps performed
  - Before/after telemetry logs

### 5. Review Process
- **Address feedback** promptly and thoughtfully
- **Keep PR updated** with latest main branch
- **Be patient** - thorough review ensures quality

## Pull Request Template

When creating a PR, please include:

```markdown
## Summary
Brief description of what this PR accomplishes.

## Changes Made
- Specific change 1
- Specific change 2
- etc.

## Environment Variables
List any new or changed environment variables:
- `NEW_VAR_NAME`: description and default value

## Testing
Manual testing performed:
- [ ] Unit tests pass (`python -m pytest -q`)
- [ ] Docker build works (`docker compose build`)
- [ ] Basic functionality verified
- [ ] Tested on E2.1.Micro (if applicable)
- [ ] Tested on A1.Flex (if applicable)
- [ ] Safety mechanisms work (load average pausing)

## Documentation
- [ ] README.md updated (if user-facing changes)
- [ ] AGENTS.md updated (if development process changes)
- [ ] Code comments added where needed

## Telemetry
Include before/after log snippets showing the change in action:
```
[Before logs]
[After logs]
```
```

## Reporting Issues

### Bug Reports
Please include:
- **Oracle Cloud shape** (E2.1.Micro, A1.Flex, etc.)
- **Platform details** (Linux distro, architecture)
- **Configuration** (relevant environment variables)
- **Expected vs. actual behavior**
- **Log output** showing the issue
- **Steps to reproduce**

### Feature Requests
Please include:
- **Use case description** - what problem does this solve?
- **Proposed solution** - how should it work?
- **Alternatives considered** - other approaches you've thought about
- **Oracle compatibility** - how does this fit Oracle's policies?

## Getting Help

- **Documentation**: Check README.md and AGENTS.md first
- **Discussions**: Use GitHub Discussions for questions
- **Issues**: Use GitHub Issues for bugs and feature requests

## Code of Conduct

Be respectful, constructive, and helpful. This project exists to help the Oracle Cloud Always Free community, so let's keep it welcoming and collaborative.

## Recognition

Contributors will be acknowledged in releases and may be added to a CONTRIBUTORS file. Significant contributions may earn maintainer status.

Thank you for helping make `loadshaper` better! 🚀