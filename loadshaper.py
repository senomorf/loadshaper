import os
import time
import random
import threading
import sqlite3
import json
import logging
import signal
import platform
import socket
import struct
from typing import Tuple, Optional, Dict, Any
from multiprocessing import Process, Value
from math import isfinite, exp, ceil
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs


# Set up module logger
logger = logging.getLogger(__name__)


# ---------------------------
# Network state management
# ---------------------------

from enum import Enum
import ipaddress


class NetworkState(Enum):
    """Network generator operational states."""
    OFF = "OFF"
    INITIALIZING = "INITIALIZING"
    VALIDATING = "VALIDATING"
    ACTIVE_UDP = "ACTIVE_UDP"
    ACTIVE_TCP = "ACTIVE_TCP"
    DEGRADED_LOCAL = "DEGRADED_LOCAL"
    ERROR = "ERROR"


class PeerState(Enum):
    """Individual peer validation states."""
    UNVALIDATED = "UNVALIDATED"
    VALID = "VALID"
    INVALID = "INVALID"
    DEGRADED = "DEGRADED"


def is_external_address(address: str) -> bool:
    """
    Check if an IP address is external (not private/local).

    Args:
        address: IP address string to check

    Returns:
        bool: True if address is external, False if private/local
    """
    try:
        ip = ipaddress.ip_address(address)

        # Reject IPv4 private/special addresses
        if isinstance(ip, ipaddress.IPv4Address):
            return not (
                ip.is_private or           # RFC 1918: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
                ip.is_loopback or          # 127.0.0.0/8
                ip.is_link_local or        # 169.254.0.0/16
                ip.is_multicast or         # 224.0.0.0/4
                ip.is_reserved or          # Various reserved ranges
                ip.is_unspecified or       # 0.0.0.0
                str(ip).startswith('100.64.')  # CGN: 100.64.0.0/10
            )

        # Reject IPv6 private/special addresses
        elif isinstance(ip, ipaddress.IPv6Address):
            return not (
                ip.is_private or           # fc00::/7 (ULA)
                ip.is_loopback or          # ::1
                ip.is_link_local or        # fe80::/10
                ip.is_multicast or         # ff00::/8
                ip.is_reserved or          # Various reserved ranges
                ip.is_unspecified or       # ::
                str(ip).startswith('2001:db8:')  # Documentation: 2001:db8::/32
            )

        return False
    except ValueError:
        # Not a valid IP address
        return False


def read_nic_tx_bytes(interface: str) -> Optional[int]:
    """
    Read transmitted bytes from network interface statistics.

    Args:
        interface: Network interface name (e.g., 'eth0')

    Returns:
        int or None: Transmitted bytes count or None if unavailable
    """
    try:
        with open(f'/sys/class/net/{interface}/statistics/tx_bytes', 'r') as f:
            return int(f.read().strip())
    except (FileNotFoundError, PermissionError, ValueError):
        return None


def build_dns_query(qname: str, qtype: int = 1, packet_size: int = 1100) -> bytes:
    """
    Build a DNS query packet with EDNS0 padding.

    Args:
        qname: Query name (e.g., "example.com")
        qtype: Query type (1=A, 28=AAAA)
        packet_size: Target packet size in bytes

    Returns:
        bytes: Complete DNS query packet
    """
    import secrets

    # DNS Header (12 bytes)
    txid = secrets.randbits(16)
    flags = 0x0100  # Standard query with recursion desired
    header = struct.pack('!HHHHHH', txid, flags, 1, 0, 0, 1)  # 1 question, 1 additional

    # Question section
    # Encode domain name as length-prefixed labels
    qname_encoded = b''
    for label in qname.split('.'):
        if label:  # Skip empty labels
            qname_encoded += bytes([len(label)]) + label.encode('ascii')
    qname_encoded += b'\x00'  # Null terminator

    question = qname_encoded + struct.pack('!HH', qtype, 1)  # qtype, qclass=IN

    # Additional section - EDNS0 OPT RR for padding
    opt_name = b'\x00'  # Root domain
    opt_type = 41       # OPT RR type
    opt_class = 1232    # UDP payload size
    opt_ttl = 0         # Extended RCODE and flags

    # Calculate padding needed
    current_size = len(header) + len(question) + 1 + 2 + 2 + 4 + 2  # OPT RR header
    padding_option_header = 4  # Option code (2) + option length (2)
    padding_needed = max(0, packet_size - current_size - padding_option_header)

    # EDNS0 padding option (code 12)
    padding_option = struct.pack('!HH', 12, padding_needed) + b'\x00' * padding_needed
    opt_rdata = padding_option

    opt_rr = opt_name + struct.pack('!HHIH', opt_type, opt_class, opt_ttl, len(opt_rdata)) + opt_rdata

    return header + question + opt_rr


# ---------------------------
# Oracle shape auto-detection
# ---------------------------

class ShapeDetectionCache:
    """
    Thread-safe cache for Oracle shape detection results with TTL.
    
    Manages caching of shape detection to avoid repeated expensive system calls
    and Oracle Cloud API requests. Uses a 5-minute TTL by default.
    """
    
    def __init__(self, ttl_seconds=300):
        """
        Initialize cache with specified TTL.
        
        Args:
            ttl_seconds (int): Time-to-live for cached results in seconds (default: 5 minutes)
        """
        self._cache = None
        self._timestamp = None
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
    
    def get_cached(self):
        """
        Get cached value if still valid.
        
        Returns:
            tuple or None: Cached shape detection result or None if expired/invalid
        """
        with self._lock:
            if (self._cache is not None and 
                self._timestamp is not None and 
                time.monotonic() - self._timestamp < self._ttl):
                return self._cache
            return None
    
    def set_cache(self, value):
        """
        Update cache with new value and current timestamp.
        
        Args:
            value (tuple): Shape detection result to cache
        """
        with self._lock:
            self._cache = value
            self._timestamp = time.monotonic()
    
    def clear_cache(self):
        """Clear cache (for testing purposes)."""
        with self._lock:
            self._cache = None
            self._timestamp = None


# Global cache instance for shape detection
_shape_cache = ShapeDetectionCache()


def detect_oracle_shape():
    """
    Detect Oracle Cloud shape based on system characteristics.
    
    Uses multiple detection methods with proper error handling:
    1. DMI system vendor information (/sys/class/dmi/id/sys_vendor)
    2. Oracle-specific file indicators (OCI tools, cloud-init metadata)
    3. System resource fingerprinting (CPU count and memory size)
    
    Returns:
        tuple: (shape_name, template_file, is_oracle)
               - shape_name: Detected shape identifier or generic description
               - template_file: Configuration template filename or None
               - is_oracle: Boolean indicating if running on Oracle Cloud
               
    Examples:
        >>> detect_oracle_shape()  # On E2.1.Micro
        ('VM.Standard.E2.1.Micro', 'e2-1-micro.env', True)
        
        >>> detect_oracle_shape()  # On non-Oracle system
        ('Generic-4CPU-8.0GB', None, False)
    """
    # Check cache validity with TTL mechanism
    cached_result = _shape_cache.get_cached()
    if cached_result is not None:
        return cached_result
    
    try:
        # Step 1: Try to detect Oracle Cloud instance via DMI/cloud metadata
        is_oracle = _detect_oracle_environment()
        
        # Step 2: Get system specifications with error handling
        cpu_count, total_mem_gb = _get_system_specs()
        
        # Step 3: Determine shape based on characteristics
        if is_oracle:
            shape_name, template_file = _classify_oracle_shape(
                cpu_count, total_mem_gb)
        else:
            shape_name = f"Generic-{cpu_count}CPU-{total_mem_gb:.1f}GB"
            template_file = None
            
        result = (shape_name, template_file, is_oracle)
        _shape_cache.set_cache(result)
        return result
            
    except Exception as e:
        # On any unexpected error, return safe fallback
        logger.warning(f"Shape detection failed: {type(e).__name__}")
        # Log full error internally but return sanitized message to prevent information disclosure
        logger.debug(f"Shape detection error details: {e}")
        fallback = (f"Unknown-Error-{type(e).__name__}", None, False)
        _shape_cache.set_cache(fallback)
        return fallback


def _detect_oracle_environment():
    """
    Detect if running on Oracle Cloud using multiple indicators.
    
    Uses three detection methods with robust error handling:
    1. DMI system vendor information (most reliable)
    2. Oracle-specific file and directory indicators
    3. Oracle metadata service connectivity check
    
    All methods handle failures gracefully, ensuring detection
    works even in restricted environments or containers.
    
    Returns:
        bool: True if Oracle Cloud environment detected, False otherwise
    """
    # Method 1: Check DMI system vendor (most reliable, requires /sys access)
    try:
        with open("/sys/class/dmi/id/sys_vendor", "r") as f:
            vendor = f.read().strip().lower()
        if "oracle" in vendor:
            return True
    except (IOError, OSError, PermissionError) as e:
        # Expected in containers or when /sys is not mounted
        pass
    
    # Method 2: Check for Oracle-specific files and directories
    oracle_indicators = [
        "/opt/oci-hpc",                    # OCI HPC tools
        "/etc/oci-hostname.conf",          # OCI hostname configuration
        "/var/lib/cloud/data/instance-id", # Cloud-init instance metadata
        "/etc/oracle-cloud-agent",         # Oracle Cloud Agent
    ]
    
    for indicator in oracle_indicators:
        try:
            if os.path.exists(indicator):
                return True
        except (OSError, PermissionError):
            # Skip indicators that can't be accessed
            continue
    
    # Method 3: Oracle-specific metadata service check (disabled by default)
    # Note: 169.254.169.254 is used by AWS/GCP/Azure, so we need Oracle-specific validation
    if os.getenv('ORACLE_METADATA_PROBE', '0').lower() in ('1', 'true', 'yes'):
        try:
            import socket
            import urllib.request
            # Try to connect and check Oracle-specific endpoint
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(0.1)  # Reduced timeout to avoid startup delays
                    result = sock.connect_ex(('169.254.169.254', 80))
                    if result == 0:  # Connection successful
                        # Verify it's actually Oracle by checking Oracle-specific endpoint
                        try:
                            req = urllib.request.Request('http://169.254.169.254/opc/v1/instance/',
                                                       headers={'User-Agent': 'loadshaper'})
                            with urllib.request.urlopen(req, timeout=0.1) as response:
                                if response.status == 200:
                                    return True
                        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
                            # Not Oracle-specific metadata service
                            pass
            except (socket.error, socket.timeout, OSError):
                # Network connection failed - expected in many environments
                pass
        except (ImportError, AttributeError):
            # Required modules unavailable in restricted environments
            pass
    
    return False


def _get_system_specs():
    """
    Get system CPU count and memory size with error handling.
    
    Returns:
        tuple: (cpu_count, total_mem_gb)
               - cpu_count: Number of CPU cores (default: 1)
               - total_mem_gb: Total memory in GB (default: 0.0)
    """
    # Get CPU count with fallback
    try:
        cpu_count = os.cpu_count() or 1
    except (AttributeError, OSError):
        cpu_count = 1
    
    # Get total memory in GB with error handling
    total_mem_gb = 0.0
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    # Parse memory value and convert from kB to GB
                    parts = line.split()
                    if len(parts) >= 2:
                        mem_kb = int(parts[1])
                        total_mem_gb = mem_kb / (1024 * 1024)
                    break
    except (IOError, OSError, ValueError, IndexError) as e:
        # /proc/meminfo parsing failed - use fallback
        logger.debug(f"Failed to read memory info: {e}")
        total_mem_gb = 0.0
    
    return cpu_count, total_mem_gb


# Memory tolerance constants for Oracle shape detection
# These ranges account for kernel memory usage and system overhead
E2_1_MICRO_MEM_RANGE = (0.8, 1.2)   # ±20% tolerance for ~1GB (E2.1.Micro)
E2_2_MICRO_MEM_RANGE = (1.8, 2.2)   # ±10% tolerance for ~2GB (E2.2.Micro)
A1_FLEX_1_MEM_RANGE = (5.5, 6.5)    # ±0.5GB tolerance for ~6GB (A1.Flex 1 vCPU)
A1_FLEX_2_MEM_RANGE = (11.5, 12.5)  # ±0.5GB tolerance for ~12GB (A1.Flex 2 vCPU)
A1_FLEX_3_MEM_RANGE = (17.5, 18.5)  # ±0.5GB tolerance for ~18GB (A1.Flex 3 vCPU)
A1_FLEX_4_MEM_RANGE = (23, 25)      # ±1GB tolerance for ~24GB (A1.Flex 4 vCPU)


def _classify_oracle_shape(cpu_count, total_mem_gb):
    """
    Classify Oracle Cloud shape based on CPU and memory characteristics.
    
    Args:
        cpu_count (int): Number of CPU cores
        total_mem_gb (float): Total memory in GB
        
    Returns:
        tuple: (shape_name, template_file)
               - shape_name: Oracle shape identifier
               - template_file: Configuration template filename
    """
    # Oracle Cloud shape classification with documented tolerances
    # E2 shapes (shared tenancy)
    if cpu_count == 1 and E2_1_MICRO_MEM_RANGE[0] <= total_mem_gb <= E2_1_MICRO_MEM_RANGE[1]:
        return ("VM.Standard.E2.1.Micro", "e2-1-micro.env")
    elif cpu_count == 2 and E2_2_MICRO_MEM_RANGE[0] <= total_mem_gb <= E2_2_MICRO_MEM_RANGE[1]:
        return ("VM.Standard.E2.2.Micro", "e2-2-micro.env")
    
    # A1.Flex shapes (dedicated Ampere)
    elif cpu_count == 1 and A1_FLEX_1_MEM_RANGE[0] <= total_mem_gb <= A1_FLEX_1_MEM_RANGE[1]:
        return ("VM.Standard.A1.Flex", "a1-flex-1.env")
    elif cpu_count == 2 and A1_FLEX_2_MEM_RANGE[0] <= total_mem_gb <= A1_FLEX_2_MEM_RANGE[1]:
        return ("VM.Standard.A1.Flex", "a1-flex-2.env")
    elif cpu_count == 3 and A1_FLEX_3_MEM_RANGE[0] <= total_mem_gb <= A1_FLEX_3_MEM_RANGE[1]:
        return ("VM.Standard.A1.Flex", "a1-flex-3.env")
    elif cpu_count == 4 and A1_FLEX_4_MEM_RANGE[0] <= total_mem_gb <= A1_FLEX_4_MEM_RANGE[1]:
        return ("VM.Standard.A1.Flex", "a1-flex-4.env")
    
    # Unknown Oracle shape - use smart fallback based on memory size
    else:
        # If memory > 4GB, likely A1.Flex variant - use A1 template to enable memory targeting
        # This ensures compliance with Oracle's 20% memory rule for A1 shapes
        if total_mem_gb > 4.0:
            return (
                f"VM.Standard.A1.Flex-Unknown-{cpu_count}CPU-{total_mem_gb:.1f}GB",
                "a1-flex-1.env"  # Use safe A1.Flex template with memory targeting
            )
        else:
            # Small memory likely E2 variant - use E2 template
            return (
                f"Oracle-Unknown-E2-{cpu_count}CPU-{total_mem_gb:.1f}GB",
                "e2-1-micro.env"
            )

def is_e2_shape() -> bool:
    """
    Detect if running on Oracle E2 shape or E2-like environment.

    E2 shapes (x86-64) have different reclamation rules than A1 shapes (ARM64).
    Oracle's Always Free tier includes specific reclamation criteria:
    - CPU utilization for the 95th percentile is less than 20%
    - Network utilization is less than 20%
    - Memory utilization is less than 20% (applies to A1 shapes only)

    Shape-specific rules:
    - For E2: Only CPU and network thresholds apply
    - For A1: All three thresholds (CPU, network, and memory) must be met

    For non-Oracle environments, uses architecture heuristics:
    - x86_64/amd64: Treated as E2-like (only CPU and network matter)
    - ARM64/aarch64: Treated as A1-like (CPU, network, and memory matter)

    Oracle documentation: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm

    Returns:
        bool: True if E2 shape or x86_64 architecture, False if A1 or ARM architecture
    """
    shape_name, _, is_oracle = detect_oracle_shape()

    if is_oracle:
        # Oracle environment - check shape name
        return shape_name and 'E2' in shape_name
    else:
        # Non-Oracle environment - use architecture heuristics
        arch = platform.machine().lower()
        return arch in ('x86_64', 'amd64')  # E2-like: x86/amd64 vs A1-like: arm/aarch64

def _validate_config_value(key, value):
    """
    Validate configuration values for security and correctness.
    
    Args:
        key (str): Configuration key name
        value (str): Configuration value to validate
        
    Raises:
        ValueError: If value is invalid for the given key
    """
    # Validate percentage values (0-100)
    if key.endswith('_PCT') or (key.startswith('CPU_P95_') and not key.endswith('_SEC')):
        try:
            pct = float(value)
            if not 0 <= pct <= 100:
                raise ValueError(f"{key}={value} must be between 0-100 (percentage)")
        except ValueError as e:
            if "must be between" in str(e):
                raise
            raise ValueError(f"{key}={value} must be a valid number (percentage)")
    
    # Validate positive numeric values with bounds checking
    elif key in ['CONTROL_PERIOD_SEC', 'AVG_WINDOW_SEC', 'MEM_MIN_FREE_MB',
                 'MEM_STEP_MB', 'MEM_TOUCH_INTERVAL_SEC', 'NET_PORT', 'NET_BURST_SEC', 'NET_IDLE_SEC',
                 'NET_LINK_MBIT', 'NET_MIN_RATE_MBIT', 'NET_MAX_RATE_MBIT',
                 'JITTER_PERIOD_SEC', 'CPU_P95_SLOT_DURATION_SEC']:
        try:
            num = float(value)
            if num <= 0:
                raise ValueError(f"{key}={value} must be positive")
            
            # Add bounds checking to prevent resource exhaustion
            bounds = {
                'CONTROL_PERIOD_SEC': (1.0, 3600.0),      # 1 second to 1 hour
                'AVG_WINDOW_SEC': (10.0, 7200.0),         # 10 seconds to 2 hours
                'MEM_MIN_FREE_MB': (50.0, 10000.0),       # 50MB to 10GB
                'MEM_STEP_MB': (1.0, 1000.0),             # 1MB to 1GB per step
                'MEM_TOUCH_INTERVAL_SEC': (0.5, 10.0),    # 0.5 to 10 seconds
                'NET_PORT': (1024.0, 65535.0),            # Valid user port range
                'NET_BURST_SEC': (1.0, 3600.0),           # 1 second to 1 hour
                'NET_IDLE_SEC': (1.0, 3600.0),            # 1 second to 1 hour
                'NET_LINK_MBIT': (1.0, 10000.0),          # 1 Mbps to 10 Gbps
                'NET_MIN_RATE_MBIT': (0.1, 10000.0),      # 0.1 Mbps to 10 Gbps
                'NET_MAX_RATE_MBIT': (1.0, 10000.0),      # 1 Mbps to 10 Gbps
                'JITTER_PERIOD_SEC': (1.0, 3600.0),       # 1 second to 1 hour
                'CPU_P95_SLOT_DURATION_SEC': (10.0, 3600.0),  # 10 seconds to 1 hour
            }
            
            if key in bounds:
                min_val, max_val = bounds[key]
                if not min_val <= num <= max_val:
                    raise ValueError(f"{key}={value} must be between {min_val}-{max_val}")
            
        except ValueError as e:
            if "must be positive" in str(e) or "must be between" in str(e):
                raise
            raise ValueError(f"{key}={value} must be a valid positive number")
    
    # Validate integer-only values
    elif key in ['NET_PORT', 'MEM_STEP_MB', 'NET_BURST_SEC', 'NET_IDLE_SEC']:
        try:
            int_value = int(float(value))
            if int_value != float(value):  # Check if it was actually an integer
                raise ValueError(f"{key}={value} must be an integer")
            
            # Specific bounds for integer fields
            int_bounds = {
                'NET_PORT': (1024, 65535),
                'MEM_STEP_MB': (1, 1000),
                'NET_BURST_SEC': (1, 3600),
                'NET_IDLE_SEC': (1, 3600),
            }
            
            if key in int_bounds:
                min_val, max_val = int_bounds[key]
                if not min_val <= int_value <= max_val:
                    raise ValueError(f"{key}={value} must be integer between {min_val}-{max_val}")
                    
        except ValueError as e:
            if "must be" in str(e):
                raise
            raise ValueError(f"{key}={value} must be a valid integer")
    
    # Validate boolean values
    elif key.endswith('_ENABLED') or key in ['LOAD_CHECK_ENABLED']:
        if value.lower() not in ['true', 'false', '1', '0']:
            raise ValueError(f"{key}={value} must be true/false or 1/0")
    
    # Validate enum values for network configuration
    elif key == 'NET_MODE':
        if value.lower() not in ['off', 'client']:
            raise ValueError(f"{key}={value} must be one of: off, client")
    elif key == 'NET_PROTOCOL':
        if value.lower() not in ['udp', 'tcp']:
            raise ValueError(f"{key}={value} must be one of: udp, tcp")
    elif key == 'NET_SENSE_MODE':
        if value.lower() not in ['container', 'host']:
            raise ValueError(f"{key}={value} must be one of: container, host")
    
    # Validate NET_PEERS IP addresses
    elif key == 'NET_PEERS':
        if value.strip():  # Only validate if not empty
            import ipaddress
            try:
                peers = [peer.strip() for peer in value.split(',')]
                for peer in peers:
                    if peer:  # Skip empty peers
                        # Try to parse as IP address (IPv4 or IPv6)
                        ipaddress.ip_address(peer)
            except (ValueError, ipaddress.AddressValueError):
                raise ValueError(f"{key}={value} contains invalid IP address. Use comma-separated IPv4/IPv6 addresses")


def load_config_template(template_file):
    """
    Load configuration from an Oracle shape template file.
    
    Parses environment variable files in KEY=VALUE format, ignoring comments
    and empty lines. Used to load shape-specific configuration templates
    that optimize loadshaper for different Oracle Cloud shapes.
    
    Args:
        template_file (str or None): Template filename (e.g., 'e2-1-micro.env')
                                   or None for no template
                                   
    Returns:
        dict: Configuration dictionary with KEY=VALUE pairs from template,
              or empty dict if template_file is None or file not found
              
    Examples:
        >>> load_config_template('e2-1-micro.env')
        {'MEM_TARGET_PCT': '60', 'NET_TARGET_PCT': '10', ...}
        
        >>> load_config_template(None)
        {}
        
    Note:
        Template files should be located in the 'config-templates/' directory
        relative to the loadshaper.py script location, or in a custom directory
        specified by the LOADSHAPER_TEMPLATE_DIR environment variable.
    """
    if not template_file:
        return {}
    
    config = {}
    
    # Allow override of template directory via environment variable
    template_dir = os.getenv("LOADSHAPER_TEMPLATE_DIR")
    if template_dir:
        template_path = os.path.join(template_dir, template_file)
    else:
        # Default: config-templates/ directory relative to this script
        template_path = os.path.join(
            os.path.dirname(__file__), "config-templates", template_file
        )
    
    try:
        with open(template_path, "r", encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                
                # Skip comments and empty lines
                if not line or line.startswith("#"):
                    continue
                    
                # Parse KEY=VALUE format
                if "=" in line:
                    try:
                        key, value = line.split("=", 1)
                        key = key.strip()
                        value = value.strip()
                        
                        # Remove inline comments from value
                        if "#" in value:
                            value = value.split("#", 1)[0].strip()
                            
                        if key and value:  # Only store non-empty keys/values
                            try:
                                # Validate the configuration value
                                _validate_config_value(key, value)
                                config[key] = value
                            except ValueError as validation_error:
                                # Log validation error but continue loading other values
                                logger.warning(f"Invalid config value at {template_file}:{line_num}: {validation_error}")
                                continue
                    except ValueError:
                        # Invalid line format - skip with warning
                        logger.warning(f"Invalid config format at {template_file}:{line_num}: {line.strip()}")
                        continue
                        
    except (IOError, OSError, UnicodeDecodeError) as e:
        # Template file not found, not readable, or encoding issues
        logger.warning(f"Could not load template {template_file}: {e}")
    
    return config

def getenv_with_template(name, default, config_template):
    """
    Get environment variable with three-tier priority fallback system.
    
    Implements the configuration priority system: 
    ENV VAR > TEMPLATE > DEFAULT
    
    This allows users to override shape-specific templates with environment
    variables while still benefiting from Oracle shape optimizations.
    
    Args:
        name (str): Environment variable name to look up
        default: Default value to use if not found in env or template
        config_template (dict): Template configuration dictionary
        
    Returns:
        str: Configuration value from highest priority source
        
    Priority Order:
        1. Environment variable (highest priority)
        2. Template configuration file
        3. Default value (lowest priority)
        
    Examples:
        >>> # ENV: MEM_TARGET_PCT=50, Template: MEM_TARGET_PCT=30
        >>> getenv_with_template('MEM_TARGET_PCT', '25', template)
        '50'  # Environment variable wins

        >>> # ENV: not set, Template: MEM_TARGET_PCT=30
        >>> getenv_with_template('MEM_TARGET_PCT', '25', template)
        '30'  # Template value used

        >>> # ENV: not set, Template: not set
        >>> getenv_with_template('MEM_TARGET_PCT', '25', {})
        '25'  # Default value used
    """
    # Priority 1: Environment variable (user override)
    env_val = os.getenv(name)
    if env_val is not None:
        return env_val
    
    # Priority 2: Template configuration (shape-specific)
    template_val = config_template.get(name)
    if template_val is not None:
        return template_val
    
    # Priority 3: Default value (fallback)
    return default

def getenv_float_with_template(name, default, config_template):
    """
    Get float environment variable with template fallback and error handling.
    
    Extends getenv_with_template() with type conversion to float and
    robust error handling for invalid numeric values.
    
    Args:
        name (str): Environment variable name
        default: Default numeric value
        config_template (dict): Template configuration dictionary
        
    Returns:
        float: Parsed float value or default if conversion fails
        
    Examples:
        >>> getenv_float_with_template('MEM_TARGET_PCT', 60.0, {'MEM_TARGET_PCT': '45.5'})
        45.5
        
        >>> getenv_float_with_template('INVALID_NUM', 30.0, {'INVALID_NUM': 'not_a_number'})
        30.0  # Falls back to default on parse error
    """
    try:
        value = getenv_with_template(name, default, config_template)
        return float(value)
    except (ValueError, TypeError) as e:
        logger.warning(f"Failed to parse {name}='{value}' as float, using default {default}: {e}")
        return float(default)

def getenv_int_with_template(name, default, config_template):
    """
    Get integer environment variable with template fallback and error handling.
    
    Extends getenv_with_template() with type conversion to int and
    robust error handling for invalid numeric values.
    
    Args:
        name (str): Environment variable name
        default: Default integer value
        config_template (dict): Template configuration dictionary
        
    Returns:
        int: Parsed integer value or default if conversion fails
        
    Examples:
        >>> getenv_int_with_template('NET_PORT', 15201, {'NET_PORT': '8080'})
        8080
        
        >>> getenv_int_with_template('INVALID_NUM', 15201, {'INVALID_NUM': 'not_a_number'})
        15201  # Falls back to default on parse error
    """
    try:
        value = getenv_with_template(name, default, config_template)
        return int(float(value))  # Allow parsing '30.0' -> 30
    except (ValueError, TypeError) as e:
        logger.warning(f"Failed to parse {name}='{value}' as int, using default {default}: {e}")
        return int(default)


def _parse_boolean(value):
    """
    Parse a boolean value from string with consistent truthy/falsy handling.
    
    Args:
        value (str): String value to parse
        
    Returns:
        bool: True for truthy values, False for falsy values
    """
    if isinstance(value, bool):
        return value
    
    value_str = str(value).strip().lower()
    return value_str in {"1", "true", "yes", "on", "enabled"}


def _validate_final_config():
    """
    Validate final configuration values including environment overrides.
    
    This ensures that even environment variable overrides are validated
    for security and correctness, logging warnings for invalid values
    and falling back to defaults where possible.
    """
    global MEM_TARGET_PCT, NET_TARGET_PCT
    global CPU_STOP_PCT, MEM_STOP_PCT, NET_STOP_PCT
    global NET_PORT, LOAD_CHECK_ENABLED
    # Validate percentage values
    for var_name, var_value in [
        ("MEM_TARGET_PCT", MEM_TARGET_PCT), 
        ("NET_TARGET_PCT", NET_TARGET_PCT),
        ("CPU_STOP_PCT", CPU_STOP_PCT),
        ("MEM_STOP_PCT", MEM_STOP_PCT),
        ("NET_STOP_PCT", NET_STOP_PCT)
    ]:
        if not (0 <= var_value <= 100):
            logger.warning(f"Invalid {var_name}={var_value} (must be 0-100%), using default")
            if "MEM_TARGET" in var_name:
                MEM_TARGET_PCT = 60.0
            elif "NET_TARGET" in var_name:
                NET_TARGET_PCT = 10.0
            elif "CPU_STOP" in var_name:
                CPU_STOP_PCT = 70.0
            elif "MEM_STOP" in var_name:
                MEM_STOP_PCT = 85.0
            elif "NET_STOP" in var_name:
                NET_STOP_PCT = 50.0
    
    # Validate NET_PORT as integer in valid range
    if not (1024 <= NET_PORT <= 65535):
        logger.warning(f"Invalid NET_PORT={NET_PORT} (must be 1024-65535), using default 15201")
        NET_PORT = 15201

    # Network fallback validation is performed after variable initialization

def _validate_network_fallback_config():
    """
    Validate network fallback configuration values.

    Called after network fallback variables are initialized to validate
    their values and reset to defaults if invalid.
    """
    # Use globals() to check and access global variables
    global_vars = globals()

    # Validate network fallback percentage values
    for var_name, default_value in [
        ("NET_FALLBACK_START_PCT", 19.0),
        ("NET_FALLBACK_STOP_PCT", 23.0),
        ("NET_FALLBACK_RISK_THRESHOLD_PCT", 22.0)
    ]:
        if var_name in global_vars:
            var_value = global_vars[var_name]
            if not (0 <= var_value <= 100):
                logger.warning(f"Invalid {var_name}={var_value} (must be 0-100%), using default")
                global_vars[var_name] = default_value

    # Validate fallback debounce and timing values (must be positive)
    for var_name, default_value in [
        ("NET_FALLBACK_DEBOUNCE_SEC", 30),
        ("NET_FALLBACK_MIN_ON_SEC", 60),
        ("NET_FALLBACK_MIN_OFF_SEC", 30),
        ("NET_FALLBACK_RAMP_SEC", 10),
    ]:
        if var_name in global_vars:
            var_value = global_vars[var_name]
            if var_value < 0:
                logger.warning(f"Invalid {var_name}={var_value} (must be >= 0), using default")
                global_vars[var_name] = default_value

    # Validate NET_ACTIVATION mode
    if "NET_ACTIVATION" in global_vars:
        valid_modes = ['adaptive', 'always', 'off']
        if global_vars["NET_ACTIVATION"] not in valid_modes:
            logger.warning(f"Invalid NET_ACTIVATION='{global_vars['NET_ACTIVATION']}' (must be one of {valid_modes}), using 'adaptive'")
            global_vars["NET_ACTIVATION"] = 'adaptive'


def _validate_p95_config():
    """
    Validate P95 CPU controller configuration values.

    Ensures CPU_P95_SETPOINT falls within the target range and provides
    front-loaded validation with clear error messages for configuration issues.
    """
    global CPU_P95_TARGET_MIN, CPU_P95_TARGET_MAX, CPU_P95_SETPOINT
    global CPU_P95_SLOT_DURATION, CONTROL_PERIOD
    global CPU_P95_BASELINE_INTENSITY, CPU_P95_HIGH_INTENSITY

    # Validate that setpoint falls within target range
    if CPU_P95_SETPOINT is not None and CPU_P95_TARGET_MIN is not None and CPU_P95_TARGET_MAX is not None:
        # Add safety margin to avoid edge cases
        safety_margin = 1.0
        min_valid = CPU_P95_TARGET_MIN + safety_margin
        max_valid = CPU_P95_TARGET_MAX - safety_margin

        if not (min_valid <= CPU_P95_SETPOINT <= max_valid):
            new_setpoint = (CPU_P95_TARGET_MIN + CPU_P95_TARGET_MAX) / 2.0
            logger.warning(f"CPU_P95_SETPOINT={CPU_P95_SETPOINT}% is outside safe range "
                          f"[{min_valid:.1f}%-{max_valid:.1f}%]. Adjusting to {new_setpoint:.1f}%.")
            CPU_P95_SETPOINT = new_setpoint

    # Validate that slot duration is reasonable relative to control period
    if CPU_P95_SLOT_DURATION is not None and CONTROL_PERIOD is not None:
        # Slot duration should be at least 6x the control period for reasonable slot management
        min_slot_ratio = 6
        if CPU_P95_SLOT_DURATION < (CONTROL_PERIOD * min_slot_ratio):
            logger.warning(f"CPU_P95_SLOT_DURATION_SEC={CPU_P95_SLOT_DURATION}s is very short relative to "
                          f"CONTROL_PERIOD_SEC={CONTROL_PERIOD}s. Consider using at least "
                          f"{CONTROL_PERIOD * min_slot_ratio}s for stable slot management.")

    # Validate that baseline intensity is less than high intensity
    if CPU_P95_BASELINE_INTENSITY is not None and CPU_P95_HIGH_INTENSITY is not None:
        if CPU_P95_BASELINE_INTENSITY >= CPU_P95_HIGH_INTENSITY:
            logger.warning(f"CPU_P95_BASELINE_INTENSITY={CPU_P95_BASELINE_INTENSITY}% must be less than "
                          f"CPU_P95_HIGH_INTENSITY={CPU_P95_HIGH_INTENSITY}%. Adjusting high intensity.")
            CPU_P95_HIGH_INTENSITY = max(CPU_P95_HIGH_INTENSITY, CPU_P95_BASELINE_INTENSITY + 1.0)
            logger.info(f"Adjusted CPU_P95_HIGH_INTENSITY to {CPU_P95_HIGH_INTENSITY:.1f}%")


# ---------------------------
# Env / config
# ---------------------------
def getenv_float(name, default):
    """Get float environment variable with fallback to default.

    Args:
        name: Environment variable name
        default: Default value if variable not set or invalid

    Returns:
        float: Environment variable value or default
    """
    try:
        return float(os.getenv(name, default))
    except Exception:
        return float(default)

def getenv_int(name, default):
    """Get integer environment variable with fallback to default.

    Args:
        name: Environment variable name
        default: Default value if variable not set or invalid

    Returns:
        int: Environment variable value or default
    """
    try:
        return int(os.getenv(name, default))
    except Exception:
        return int(default)

# Configuration variables (initialized lazily to avoid issues during testing)
_config_initialized = False
DETECTED_SHAPE = None
TEMPLATE_FILE = None
IS_ORACLE = None
CONFIG_TEMPLATE = {}

MEM_TARGET_PCT = None
NET_TARGET_PCT = None
CPU_STOP_PCT = None
MEM_STOP_PCT = None
NET_STOP_PCT = None
CONTROL_PERIOD = None
AVG_WINDOW_SEC = None
HYSTERESIS_PCT = None
LOAD_THRESHOLD = None
LOAD_RESUME_THRESHOLD = None
LOAD_CHECK_ENABLED = None
JITTER_PCT = None
JITTER_PERIOD = None
MEM_MIN_FREE_MB = None

# P95-driven CPU control globals
CPU_P95_TARGET_MIN = None
CPU_P95_TARGET_MAX = None
CPU_P95_SETPOINT = None
CPU_P95_EXCEEDANCE_TARGET = None
CPU_P95_SLOT_DURATION = None
CPU_P95_HIGH_INTENSITY = None
CPU_P95_BASELINE_INTENSITY = None
MEM_STEP_MB = None
MEM_TOUCH_INTERVAL_SEC = None
NET_MODE = None
NET_PEERS = None
NET_PORT = None
NET_BURST_SEC = None
NET_IDLE_SEC = None
NET_PROTOCOL = None
NET_SENSE_MODE = None
NET_IFACE = None
NET_IFACE_INNER = None
NET_LINK_MBIT = None
NET_MIN_RATE = None
NET_MAX_RATE = None
NET_TTL = None
NET_PACKET_SIZE = None

# Network fallback configuration globals
NET_ACTIVATION = None
NET_FALLBACK_START_PCT = None
NET_FALLBACK_STOP_PCT = None
NET_FALLBACK_RISK_THRESHOLD_PCT = None
NET_FALLBACK_DEBOUNCE_SEC = None
NET_FALLBACK_MIN_ON_SEC = None
NET_FALLBACK_MIN_OFF_SEC = None
NET_FALLBACK_RAMP_SEC = None

# Control shared variables
paused = None


def _initialize_config():
    """
    Initialize configuration variables lazily to avoid issues during testing.
    
    This function is called on first access to configuration variables to ensure
    Oracle shape detection and template loading happens only when needed, not
    during module import.
    """
    global _config_initialized, DETECTED_SHAPE, TEMPLATE_FILE, IS_ORACLE, CONFIG_TEMPLATE
    global MEM_TARGET_PCT, NET_TARGET_PCT
    global CPU_STOP_PCT, MEM_STOP_PCT, NET_STOP_PCT
    global CONTROL_PERIOD, AVG_WINDOW_SEC, HYSTERESIS_PCT
    global LOAD_THRESHOLD, LOAD_RESUME_THRESHOLD, LOAD_CHECK_ENABLED
    global JITTER_PCT, JITTER_PERIOD, MEM_MIN_FREE_MB, MEM_STEP_MB, MEM_TOUCH_INTERVAL_SEC
    global CPU_P95_TARGET_MIN, CPU_P95_TARGET_MAX, CPU_P95_SETPOINT, CPU_P95_EXCEEDANCE_TARGET
    global CPU_P95_SLOT_DURATION, CPU_P95_HIGH_INTENSITY, CPU_P95_BASELINE_INTENSITY
    global NET_ACTIVATION, NET_FALLBACK_START_PCT, NET_FALLBACK_STOP_PCT, NET_FALLBACK_RISK_THRESHOLD_PCT
    global NET_FALLBACK_DEBOUNCE_SEC, NET_FALLBACK_MIN_ON_SEC, NET_FALLBACK_MIN_OFF_SEC, NET_FALLBACK_RAMP_SEC
    global NET_MODE, NET_PEERS, NET_PORT, NET_BURST_SEC, NET_IDLE_SEC, NET_PROTOCOL
    global NET_SENSE_MODE, NET_IFACE, NET_IFACE_INNER, NET_LINK_MBIT
    global NET_MIN_RATE, NET_MAX_RATE
    
    if _config_initialized:
        return
    
    # Initialize Oracle shape detection and template loading
    DETECTED_SHAPE, TEMPLATE_FILE, IS_ORACLE = detect_oracle_shape()
    CONFIG_TEMPLATE = load_config_template(TEMPLATE_FILE)

    MEM_TARGET_PCT    = getenv_float_with_template("MEM_TARGET_PCT", 60.0, CONFIG_TEMPLATE)  # excludes cache/buffers
    NET_TARGET_PCT    = getenv_float_with_template("NET_TARGET_PCT", 10.0, CONFIG_TEMPLATE)  # NIC utilization %

    CPU_STOP_PCT      = getenv_float_with_template("CPU_STOP_PCT", 85.0, CONFIG_TEMPLATE)
    MEM_STOP_PCT      = getenv_float_with_template("MEM_STOP_PCT", 90.0, CONFIG_TEMPLATE)
    NET_STOP_PCT      = getenv_float_with_template("NET_STOP_PCT", 60.0, CONFIG_TEMPLATE)

    CONTROL_PERIOD    = getenv_float_with_template("CONTROL_PERIOD_SEC", 5.0, CONFIG_TEMPLATE)
    AVG_WINDOW_SEC    = getenv_float_with_template("AVG_WINDOW_SEC", 300.0, CONFIG_TEMPLATE)
    HYSTERESIS_PCT    = getenv_float_with_template("HYSTERESIS_PCT", 5.0, CONFIG_TEMPLATE)

    # LOAD AVERAGE THRESHOLDS: Conservative values for Oracle Free Tier protection
    # 0.6 per core = 60% sustained load triggers pause (protects legitimate workloads)
    # 0.4 per core = 40% resume threshold (hysteresis prevents oscillation)
    # Values are conservative because Free Tier VMs have limited resources and
    # any interference with legitimate workloads defeats the purpose of the service.
    LOAD_THRESHOLD    = getenv_float_with_template("LOAD_THRESHOLD", 0.6, CONFIG_TEMPLATE)      # CPU contention detection threshold
    LOAD_RESUME_THRESHOLD = getenv_float_with_template("LOAD_RESUME_THRESHOLD", 0.4, CONFIG_TEMPLATE)  # Hysteresis gap for stability
    LOAD_CHECK_ENABLED = _parse_boolean(getenv_with_template("LOAD_CHECK_ENABLED", "true", CONFIG_TEMPLATE))

    # P95-driven CPU control configuration
    # CRITICAL FOR ORACLE COMPLIANCE: Oracle Free Tier VMs are reclaimed when ALL metrics
    # stay below 20% for 7 consecutive days. Oracle measures CPU using 95th percentile.
    # Target range 22-28% provides safe buffer above 20% reclamation threshold while avoiding
    # excessive resource usage that could impact legitimate workloads.
    CPU_P95_TARGET_MIN = getenv_float_with_template("CPU_P95_TARGET_MIN", 22.0, CONFIG_TEMPLATE)  # Oracle compliance floor: must stay >20% P95
    CPU_P95_TARGET_MAX = getenv_float_with_template("CPU_P95_TARGET_MAX", 28.0, CONFIG_TEMPLATE)  # Efficiency ceiling: avoids excessive usage
    CPU_P95_SETPOINT   = getenv_float_with_template("CPU_P95_SETPOINT", 25.0, CONFIG_TEMPLATE)    # Optimal target: center of safe range
    CPU_P95_EXCEEDANCE_TARGET = getenv_float_with_template("CPU_P95_EXCEEDANCE_TARGET", 6.5, CONFIG_TEMPLATE)  # Target % of high slots (>5% ensures P95>baseline)
    CPU_P95_SLOT_DURATION = getenv_float_with_template("CPU_P95_SLOT_DURATION_SEC", 60.0, CONFIG_TEMPLATE)  # Duration of each slot in seconds
    CPU_P95_HIGH_INTENSITY = getenv_float_with_template("CPU_P95_HIGH_INTENSITY", 35.0, CONFIG_TEMPLATE)  # CPU % during high slots
    CPU_P95_BASELINE_INTENSITY = getenv_float_with_template("CPU_P95_BASELINE_INTENSITY", 20.0, CONFIG_TEMPLATE)  # CPU % during normal slots (minimum for P95>20%)

    JITTER_PCT        = getenv_float_with_template("JITTER_PCT", 10.0, CONFIG_TEMPLATE)
    JITTER_PERIOD     = getenv_float_with_template("JITTER_PERIOD_SEC", 5.0, CONFIG_TEMPLATE)

    MEM_MIN_FREE_MB   = getenv_int_with_template("MEM_MIN_FREE_MB", 512, CONFIG_TEMPLATE)
    MEM_STEP_MB       = getenv_int_with_template("MEM_STEP_MB", 64, CONFIG_TEMPLATE)
    MEM_TOUCH_INTERVAL_SEC = getenv_float_with_template("MEM_TOUCH_INTERVAL_SEC", 1.0, CONFIG_TEMPLATE)

    NET_MODE          = getenv_with_template("NET_MODE", "client", CONFIG_TEMPLATE).strip().lower()
    NET_PEERS         = [p.strip() for p in getenv_with_template("NET_PEERS", "8.8.8.8,1.1.1.1,9.9.9.9", CONFIG_TEMPLATE).split(",") if p.strip()]
    NET_PORT          = getenv_int_with_template("NET_PORT", 15201, CONFIG_TEMPLATE)
    NET_BURST_SEC     = getenv_int_with_template("NET_BURST_SEC", 10, CONFIG_TEMPLATE)
    NET_IDLE_SEC      = getenv_int_with_template("NET_IDLE_SEC", 10, CONFIG_TEMPLATE)
    NET_PROTOCOL      = getenv_with_template("NET_PROTOCOL", "udp", CONFIG_TEMPLATE).strip().lower()

    # NIC bytes sensing configuration
    NET_SENSE_MODE    = getenv_with_template("NET_SENSE_MODE", "container", CONFIG_TEMPLATE).strip().lower()  # container|host
    NET_IFACE         = getenv_with_template("NET_IFACE", "ens3", CONFIG_TEMPLATE).strip()        # for host mode (requires /sys mount)
    NET_IFACE_INNER   = getenv_with_template("NET_IFACE_INNER", "eth0", CONFIG_TEMPLATE).strip()  # for container mode (/proc/net/dev)
    NET_LINK_MBIT     = getenv_float_with_template("NET_LINK_MBIT", 1000.0, CONFIG_TEMPLATE)         # used directly in container mode

    # Controller rate bounds (Mbps)
    NET_MIN_RATE      = getenv_float_with_template("NET_MIN_RATE_MBIT", 1.0, CONFIG_TEMPLATE)
    NET_MAX_RATE      = getenv_float_with_template("NET_MAX_RATE_MBIT", 800.0, CONFIG_TEMPLATE)

    # Native network generator configuration
    NET_TTL           = getenv_int_with_template("NET_TTL", 1, CONFIG_TEMPLATE)
    NET_PACKET_SIZE   = getenv_int_with_template("NET_PACKET_SIZE", 1100, CONFIG_TEMPLATE)  # Reduced for DNS compatibility

    # Network validation and reliability configuration
    NET_VALIDATE_STARTUP = getenv_with_template("NET_VALIDATE_STARTUP", "true", CONFIG_TEMPLATE).strip().lower() in ['true', '1', 'yes']
    NET_REQUIRE_EXTERNAL = getenv_with_template("NET_REQUIRE_EXTERNAL", "true", CONFIG_TEMPLATE).strip().lower() in ['true', '1', 'yes']
    NET_VALIDATION_TIMEOUT_MS = getenv_int_with_template("NET_VALIDATION_TIMEOUT_MS", 200, CONFIG_TEMPLATE)
    NET_TX_BYTES_MIN_DELTA = getenv_int_with_template("NET_TX_BYTES_MIN_DELTA", 1000, CONFIG_TEMPLATE)
    NET_STATE_DEBOUNCE_SEC = getenv_float_with_template("NET_STATE_DEBOUNCE_SEC", 5.0, CONFIG_TEMPLATE)
    NET_STATE_MIN_ON_SEC = getenv_float_with_template("NET_STATE_MIN_ON_SEC", 15.0, CONFIG_TEMPLATE)
    NET_STATE_MIN_OFF_SEC = getenv_float_with_template("NET_STATE_MIN_OFF_SEC", 20.0, CONFIG_TEMPLATE)
    NET_DNS_QPS_MAX = getenv_float_with_template("NET_DNS_QPS_MAX", 10.0, CONFIG_TEMPLATE)
    NET_IPV6 = getenv_with_template("NET_IPV6", "auto", CONFIG_TEMPLATE).strip().lower()

    # Network fallback configuration
    NET_ACTIVATION          = getenv_with_template("NET_ACTIVATION", "adaptive", CONFIG_TEMPLATE).strip().lower()
    NET_FALLBACK_START_PCT  = getenv_float_with_template("NET_FALLBACK_START_PCT", 19.0, CONFIG_TEMPLATE)
    NET_FALLBACK_STOP_PCT   = getenv_float_with_template("NET_FALLBACK_STOP_PCT", 23.0, CONFIG_TEMPLATE)
    NET_FALLBACK_RISK_THRESHOLD_PCT = getenv_float_with_template("NET_FALLBACK_RISK_THRESHOLD_PCT", 22.0, CONFIG_TEMPLATE)
    NET_FALLBACK_DEBOUNCE_SEC = getenv_int_with_template("NET_FALLBACK_DEBOUNCE_SEC", 30, CONFIG_TEMPLATE)
    NET_FALLBACK_MIN_ON_SEC = getenv_int_with_template("NET_FALLBACK_MIN_ON_SEC", 60, CONFIG_TEMPLATE)
    NET_FALLBACK_MIN_OFF_SEC = getenv_int_with_template("NET_FALLBACK_MIN_OFF_SEC", 30, CONFIG_TEMPLATE)
    NET_FALLBACK_RAMP_SEC = getenv_int_with_template("NET_FALLBACK_RAMP_SEC", 10, CONFIG_TEMPLATE)

    # Validate final configuration values (including environment overrides)
    _validate_final_config()
    _validate_network_fallback_config()
    _validate_p95_config()
    
    _config_initialized = True

# Health check server configuration
HEALTH_PORT       = getenv_int("HEALTH_PORT", 8080)
HEALTH_HOST       = os.getenv("HEALTH_HOST", "127.0.0.1").strip()
HEALTH_ENABLED    = _parse_boolean(os.getenv("HEALTH_ENABLED", "true"))

# Workers equal to CPU count for smoother shaping
N_WORKERS = os.cpu_count() or 1

# Controller gains (gentle)
KP_CPU = 0.30       # proportional gain for CPU duty
KP_NET = 0.60       # proportional gain for network generation rate (Mbps)
MAX_DUTY = 0.95     # CPU duty cap

# Sleep slice for yielding scheduler - critical for system responsiveness
# 5ms chosen as balance between CPU utilization accuracy and responsiveness:
# - Long enough to avoid excessive context switching overhead
# - Short enough to ensure other processes get timely CPU access
SLEEP_SLICE = 0.005

class CPUP95Controller:
    """
    P95-driven CPU controller implementing Oracle's exact reclamation criteria.

    Uses exceedance budget control: maintains approximately 6.5% of time slots above threshold
    to achieve target P95. Implements state machine based on 7-day P95 trends.
    """

    # State machine timing constants
    STATE_CHANGE_COOLDOWN_SEC = 300  # 5 minutes cooldown after state change
    P95_CACHE_TTL_SEC = 300          # Cache P95 calculations for 5 minutes (aligned with state change cooldown)
    PERSISTENT_STORAGE_PATH = "/var/lib/loadshaper"  # Persistent storage directory for metrics DB and ring buffer

    # Hysteresis values for adaptive deadbands
    HYSTERESIS_SMALL_PCT = 0.5       # Small hysteresis for stable periods
    HYSTERESIS_MEDIUM_PCT = 1.0      # Medium hysteresis (stable operation)
    HYSTERESIS_LARGE_PCT = 2.0       # Large hysteresis
    HYSTERESIS_XLARGE_PCT = 2.5      # Extra large hysteresis after state change

    # State maintain buffer zones (require being well within range to transition)
    MAINTAIN_BUFFER_SMALL = 0.5      # Buffer when using small hysteresis
    MAINTAIN_BUFFER_MEDIUM = 1.5     # Buffer when using medium hysteresis
    MAINTAIN_BUFFER_LARGE = 2.0      # Buffer when using large hysteresis

    # Distance thresholds for aggressive adjustments
    DISTANCE_THRESHOLD_LARGE = 5.0   # Far from target threshold for aggressive action
    DISTANCE_THRESHOLD_XLARGE = 10.0 # Very far from target threshold for maximum aggression

    # Intensity adjustments for BUILDING state
    BUILD_AGGRESSIVE_INTENSITY_BOOST = 8.0  # Very aggressive catch-up when far below
    BUILD_NORMAL_INTENSITY_BOOST = 5.0      # Normal catch-up intensity boost

    # Intensity adjustments for REDUCING state
    REDUCE_AGGRESSIVE_INTENSITY_CUT = 5.0   # Conservative high when way above target
    REDUCE_MODERATE_INTENSITY_CUT = 2.0     # Moderate reduction

    # Proportional control gain for MAINTAINING state
    MAINTAIN_PROPORTIONAL_GAIN = 0.2        # Proportional adjustment factor for setpoint control

    # Dithering for micro-variations in production
    DITHER_RANGE_PCT = 1.0                  # ±1% random variation for better P95 control

    # Exceedance target adjustments for BUILDING state
    BUILD_AGGRESSIVE_EXCEEDANCE_BOOST = 4.0 # When far below target
    BUILD_NORMAL_EXCEEDANCE_BOOST = 1.0     # Normal building boost

    # Exceedance targets for REDUCING state
    REDUCE_AGGRESSIVE_EXCEEDANCE_TARGET = 1.0   # Very low for fast reduction
    REDUCE_MODERATE_EXCEEDANCE_TARGET = 2.5     # Moderate reduction

    # Exceedance caps and adjustments
    EXCEEDANCE_SAFETY_CAP = 12.0            # Maximum exceedance percentage for safety
    MAINTAIN_EXCEEDANCE_ADJUSTMENT = 0.5    # Fine adjustment in maintaining state

    # Safety-driven proportional scaling constants
    SAFETY_PROPORTIONAL_ENABLED = True     # Enable proportional scaling based on load
    SAFETY_SCALE_START = 0.5               # Load level where scaling starts (below resume threshold)
    SAFETY_SCALE_FULL = 0.8                # Load level where full baseline is applied
    SAFETY_MIN_INTENSITY_SCALE = 0.7       # Minimum scaling factor (70% of normal intensity)

    # Setpoint bounds for safety
    SETPOINT_SAFETY_MARGIN = 1.0            # Stay 1% away from target boundaries

    def __init__(self, metrics_storage):
        """Initialize the P95-driven CPU controller.

        Args:
            metrics_storage: MetricsStorage instance for accessing historical P95 data

        Sets up state machine, slot tracking, and initializes 24-hour ring buffer
        for fast exceedance budget control based on Oracle compliance requirements.

        IMPORTANT: This controller assumes only one LoadShaper process runs per system.
        Multiple concurrent instances would create race conditions in the ring buffer
        file writes and could corrupt P95 state persistence.
        """
        self.metrics_storage = metrics_storage
        self._lock = threading.RLock()  # Thread-safe access to shared state
        self.state = 'MAINTAINING'
        self.last_state_change = time.monotonic()
        self.current_slot_start = time.monotonic()
        self.current_slot_is_high = False
        self.current_target_intensity = CPU_P95_BASELINE_INTENSITY
        self.slots_skipped_safety = 0

        # Sustained high-load fallback mechanism
        self.consecutive_skipped_slots = 0
        self.last_high_slot_time = time.monotonic()

        # Fallback thresholds
        self.MAX_CONSECUTIVE_SKIPPED_SLOTS = 120  # 2 hours at 60s slots
        self.MIN_HIGH_SLOT_INTERVAL_SEC = 3600   # Force one high slot per hour minimum

        # Ring buffer for last 24h of slot data (fast control)
        # Calculate size dynamically based on slot duration: 86400 seconds / slot_duration
        self.slot_history_size = max(1, int(ceil(86400.0 / CPU_P95_SLOT_DURATION)))
        self.slot_history = [False] * self.slot_history_size  # True = high slot
        self.slot_history_index = 0
        self.slots_recorded = 0

        # P95 caching to reduce database queries (performance optimization)
        self._p95_cache = None
        self._p95_cache_time = 0
        self._p95_cache_ttl_sec = self.P95_CACHE_TTL_SEC

        # Ring buffer persistence optimization (reduce disk I/O)
        self.slots_since_last_save = 0

        # Try to load persisted ring buffer state to solve cold start problem
        self._load_ring_buffer_state()

        # Initialize first slot (no load average available yet)
        self._start_new_slot(current_load_avg=None)

    def get_cpu_p95(self):
        """Get 7-day CPU P95 from metrics storage with caching"""
        with self._lock:
            now = time.monotonic()

            # Return cached value if still valid
            if self._p95_cache is not None and (now - self._p95_cache_time) < self._p95_cache_ttl_sec:
                return self._p95_cache

            # Query database for fresh P95 value
            p95 = self.metrics_storage.get_percentile('cpu', percentile=95)

            # Only update cache if a valid value is returned
            if p95 is not None:
                self._p95_cache = p95
                self._p95_cache_time = now
                return p95

            # Fallback: If DB returns None but we have a cached value, use it
            # This prevents controller from losing P95 input during temporary DB issues
            if self._p95_cache is not None:
                logger.debug("P95 controller: Using cached P95 value due to database read failure")
                return self._p95_cache

            # Last resort: No data available
            return None

    def _get_ring_buffer_path(self):
        """Get path for ring buffer persistence file"""
        # Use same directory as metrics database for consistency
        db_dir = self.PERSISTENT_STORAGE_PATH
        if not os.path.isdir(db_dir):
            raise FileNotFoundError(f"P95 ring buffer directory does not exist: {db_dir}. "
                                    f"A persistent volume must be mounted.")
        if not os.access(db_dir, os.W_OK):
            raise PermissionError(f"Cannot write to P95 ring buffer directory: {db_dir}. "
                                  f"Check volume permissions for persistent storage.")
        return os.path.join(db_dir, "p95_ring_buffer.json")

    def _save_ring_buffer_state(self):
        """Save ring buffer state to disk for persistence across restarts"""
        # Skip persistence in test mode for predictable test behavior
        if os.environ.get('PYTEST_CURRENT_TEST'):
            return

        try:
            ring_buffer_path = self._get_ring_buffer_path()
            temp_path = ring_buffer_path + '.tmp'
            state = {
                'slot_history': self.slot_history,
                'slot_history_index': self.slot_history_index,
                'slots_recorded': self.slots_recorded,
                'slot_history_size': self.slot_history_size,
                'timestamp': time.time()  # Use wall clock time for persistence
            }

            # Write to temporary file first for atomic operation
            with open(temp_path, 'w') as f:
                json.dump(state, f)

            # Atomically replace the target file
            os.replace(temp_path, ring_buffer_path)

            logger.debug(f"Saved P95 ring buffer state to {ring_buffer_path}")

        except (OSError, PermissionError, ValueError, TypeError) as e:
            logger.warning(f"Failed to save P95 ring buffer state: {e}")
            # Clean up temp file if it exists
            try:
                temp_path = self._get_ring_buffer_path() + '.tmp'
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            except Exception:
                pass  # Ignore cleanup errors
            # Non-fatal error - continue operation without persistence

    def _load_ring_buffer_state(self):
        """Load ring buffer state from disk if available and recent"""
        # Skip persistence in test mode for predictable test behavior
        if os.environ.get('PYTEST_CURRENT_TEST'):
            logger.debug("Skipping ring buffer loading in test mode")
            return

        try:
            ring_buffer_path = self._get_ring_buffer_path()

            if not os.path.exists(ring_buffer_path):
                logger.info("No persisted P95 ring buffer state found, starting fresh")
                return

            with open(ring_buffer_path, 'r') as f:
                state = json.load(f)

            # Validate state age - only use if less than 2 hours old
            # Note: Ring buffer validity (2h) is intentionally much longer than P95 cache TTL (5min)
            # This allows cold start recovery while ensuring fresh P95 data drives control decisions
            state_age_hours = (time.time() - state.get('timestamp', 0)) / 3600
            if state_age_hours > 2:
                logger.debug(f"P95 ring buffer state too old ({state_age_hours:.1f}h), ignoring")
                return

            # Validate state structure and size consistency
            expected_size = max(1, int(ceil(86400.0 / CPU_P95_SLOT_DURATION)))
            if (state.get('slot_history_size') != expected_size or
                len(state.get('slot_history', [])) != expected_size):
                logger.debug("P95 ring buffer state size mismatch, ignoring")
                return

            # Restore state
            self.slot_history = state['slot_history']
            self.slot_history_index = state['slot_history_index']
            self.slots_recorded = state['slots_recorded']

            logger.info(f"Restored P95 ring buffer state ({self.slots_recorded}/{self.slot_history_size} slots, age={state_age_hours:.1f}h)")

        except (OSError, PermissionError, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug(f"Failed to load P95 ring buffer state: {e}")
            # Non-fatal error - continue with fresh ring buffer

    def update_state(self, cpu_p95):
        """Update state machine with adaptive thresholds based on current CPU P95"""
        with self._lock:
            if cpu_p95 is None:
                return  # No P95 data yet

            old_state = self.state
            now = time.monotonic()

            # Adaptive hysteresis based on recent state changes (prevents oscillation)
            time_since_change = now - self.last_state_change
            if time_since_change < self.STATE_CHANGE_COOLDOWN_SEC:  # Recent change - larger deadband
                hysteresis = self.HYSTERESIS_XLARGE_PCT
                maintain_buffer = self.MAINTAIN_BUFFER_LARGE
            else:  # Stable - smaller deadband for faster response
                hysteresis = self.HYSTERESIS_MEDIUM_PCT
                maintain_buffer = self.MAINTAIN_BUFFER_SMALL

            # State transitions with adaptive hysteresis
            if cpu_p95 < (CPU_P95_TARGET_MIN - hysteresis):
                self.state = 'BUILDING'
            elif cpu_p95 > (CPU_P95_TARGET_MAX + hysteresis):
                self.state = 'REDUCING'
            elif CPU_P95_TARGET_MIN <= cpu_p95 <= CPU_P95_TARGET_MAX:
                # Only transition to MAINTAINING if we're in the target range
                if self.state in ['BUILDING', 'REDUCING']:
                    # Add adaptive hysteresis - need to be well within range to transition
                    if (CPU_P95_TARGET_MIN + maintain_buffer) <= cpu_p95 <= (CPU_P95_TARGET_MAX - maintain_buffer):
                        self.state = 'MAINTAINING'

            if old_state != self.state:
                self.last_state_change = now
                logger.info(f"CPU P95 controller state: {old_state} → {self.state} (P95={cpu_p95:.1f}%, hysteresis={hysteresis:.1f}%)")

    def get_target_intensity(self):
        """Get target CPU intensity based on current state and distance from target"""
        with self._lock:
            cpu_p95 = self.get_cpu_p95()

            if self.state == 'BUILDING':
                # More aggressive if further from target
                if cpu_p95 is not None and cpu_p95 < (CPU_P95_TARGET_MIN - self.DISTANCE_THRESHOLD_LARGE):
                    computed = CPU_P95_HIGH_INTENSITY + self.BUILD_AGGRESSIVE_INTENSITY_BOOST  # Very aggressive catch-up
                else:
                    computed = CPU_P95_HIGH_INTENSITY + self.BUILD_NORMAL_INTENSITY_BOOST  # Normal catch-up
            elif self.state == 'REDUCING':
                # More conservative if way above target, but keep high slots truly high
                # Reduction comes from lower exceedance frequency, not making high slots low
                if cpu_p95 is not None and cpu_p95 > (CPU_P95_TARGET_MAX + self.DISTANCE_THRESHOLD_XLARGE):
                    computed = CPU_P95_HIGH_INTENSITY - self.REDUCE_AGGRESSIVE_INTENSITY_CUT  # Conservative high intensity
                else:
                    computed = CPU_P95_HIGH_INTENSITY - self.REDUCE_MODERATE_INTENSITY_CUT  # Moderate reduction
            else:  # MAINTAINING
                # High intensity targeting aligns with P95 setpoint for precise control
                # When exceedance > 5%, P95 collapses to high intensity value, so we want
                # high intensity to equal our target setpoint to achieve precise control
                setpoint = CPU_P95_SETPOINT if CPU_P95_SETPOINT is not None else (CPU_P95_TARGET_MIN + CPU_P95_TARGET_MAX) / 2
                if cpu_p95 is not None:
                    # Use setpoint as the base, with small adjustments based on current P95
                    error = cpu_p95 - setpoint
                    # Small proportional adjustment: if we're below setpoint, increase intensity slightly
                    adjustment = -error * self.MAINTAIN_PROPORTIONAL_GAIN  # Negative because we want inverse relationship
                    computed = setpoint + adjustment
                    # Clamp to reasonable bounds around setpoint
                    computed = max(CPU_P95_TARGET_MIN + self.SETPOINT_SAFETY_MARGIN,
                                  min(CPU_P95_TARGET_MAX - self.SETPOINT_SAFETY_MARGIN, computed))
                else:
                    computed = setpoint  # Default to setpoint when no P95 data

            # CRITICAL: Ensure high intensity is always >= baseline (never below baseline)
            base_intensity = max(CPU_P95_BASELINE_INTENSITY, computed)

            # Add small dithering to break up step behavior (±1% random variation)
            # This creates micro-variations in high slots that help achieve mid-range P95 targets
            # Skip dithering during tests (deterministic behavior for tests)
            if os.environ.get('PYTEST_CURRENT_TEST'):
                # In test mode - return exact values for predictable tests
                return base_intensity
            else:
                # In production mode - add dithering for better P95 control
                dither = random.uniform(-self.DITHER_RANGE_PCT, self.DITHER_RANGE_PCT)
                dithered_intensity = base_intensity + dither
                # Ensure we stay within reasonable bounds after dithering
                return max(CPU_P95_BASELINE_INTENSITY, min(100.0, dithered_intensity))

    def get_exceedance_target(self):
        """Get adaptive exceedance target based on state and P95 distance from target"""
        with self._lock:
            cpu_p95 = self.get_cpu_p95()
            base_target = CPU_P95_EXCEEDANCE_TARGET

            if self.state == 'BUILDING':
                # Higher exceedance if we're far below target
                if cpu_p95 is not None and cpu_p95 < (CPU_P95_TARGET_MIN - self.DISTANCE_THRESHOLD_LARGE):
                    return min(self.EXCEEDANCE_SAFETY_CAP, base_target + self.BUILD_AGGRESSIVE_EXCEEDANCE_BOOST)  # Cap for safety
                else:
                    return base_target + self.BUILD_NORMAL_EXCEEDANCE_BOOST  # Slightly higher for building
            elif self.state == 'REDUCING':
                # Lower exceedance, more aggressive if way above target
                if cpu_p95 is not None and cpu_p95 > (CPU_P95_TARGET_MAX + self.DISTANCE_THRESHOLD_XLARGE):
                    return self.REDUCE_AGGRESSIVE_EXCEEDANCE_TARGET  # Very low for fast reduction
                else:
                    return self.REDUCE_MODERATE_EXCEEDANCE_TARGET  # Moderate reduction
            else:  # MAINTAINING
                # Keep exceedance stable - only adjust intensity for control in MAINTAINING state
                # This prevents dual-variable control which can cause instability
                return base_target

    def should_run_high_slot(self, current_load_avg):
        """Determine if this slot should be high intensity (slot-based control)"""
        with self._lock:
            now = time.monotonic()

            # Handle multiple slot rollovers if process stalled
            while now >= (self.current_slot_start + CPU_P95_SLOT_DURATION):
                self._end_current_slot()
                # Advance slot start time by duration to properly account for missed slots
                self.current_slot_start += CPU_P95_SLOT_DURATION
                self._start_new_slot(current_load_avg, self.current_slot_start)

            return self.current_slot_is_high, self.current_target_intensity

    def _end_current_slot(self):
        """End current slot and record its type in history"""
        # Record slot in ring buffer (24-hour sliding window for fast exceedance calculations)
        # Ring buffer avoids expensive database queries for recent slot history
        self.slot_history[self.slot_history_index] = self.current_slot_is_high
        self.slot_history_index = (self.slot_history_index + 1) % self.slot_history_size
        if self.slots_recorded < self.slot_history_size:
            self.slots_recorded += 1  # Don't exceed buffer size

        # Persist ring buffer state periodically for cold start protection (reduce disk I/O)
        self.slots_since_last_save += 1
        if self.slots_since_last_save >= 10:
            self._save_ring_buffer_state()
            self.slots_since_last_save = 0

    def _start_new_slot(self, current_load_avg, slot_start_time=None):
        """Start new slot and determine its type

        Args:
            current_load_avg: Current system load average
            slot_start_time: Optional explicit start time (for rollover scenarios)
        """
        if slot_start_time is not None:
            self.current_slot_start = slot_start_time
        else:
            self.current_slot_start = time.monotonic()
        now = time.monotonic()

        # Check if we need to force a high slot due to sustained blocking
        time_since_high_slot = now - self.last_high_slot_time
        force_high_slot = (self.consecutive_skipped_slots >= self.MAX_CONSECUTIVE_SKIPPED_SLOTS or
                          time_since_high_slot >= self.MIN_HIGH_SLOT_INTERVAL_SEC)

        # Safety check - scale intensity based on system load (only if load checking is enabled)
        # But allow forced high slots to override safety when P95 protection is at risk
        if (LOAD_CHECK_ENABLED and current_load_avg is not None and
            current_load_avg > LOAD_THRESHOLD and not force_high_slot):
            self.slots_skipped_safety += 1
            self.consecutive_skipped_slots += 1
            self.current_slot_is_high = False
            normal_intensity = self.get_target_intensity()
            self.current_target_intensity = self._calculate_safety_scaled_intensity(current_load_avg, normal_intensity)
            logger.debug(f"P95 controller: skipped slot due to load (consecutive={self.consecutive_skipped_slots}, time_since_high={time_since_high_slot:.0f}s)")
            return

        # Calculate current exceedance from recent slot history
        # Exceedance = percentage of slots that were high intensity
        # This is the core metric for controlling P95: approximately 6.5% of slots
        # should be high intensity to achieve target P95 above the baseline
        current_exceedance = self._calculate_current_exceedance()

        # Get target exceedance based on state (adaptive based on distance from P95 target)
        exceedance_target = self.get_exceedance_target() / 100.0

        # Decide slot type based on exceedance budget control or forced fallback
        # Key insight: We "spend" our exceedance budget (6.5%) by running high slots
        # If we're under budget, we can afford to run high; if over budget, run baseline
        # But force high slots when necessary to prevent P95 collapse during sustained load
        if force_high_slot:
            self.current_slot_is_high = True
            normal_intensity = self.get_target_intensity()
            self.current_target_intensity = normal_intensity
            # Use reduced intensity for forced slots to minimize system impact
            if current_load_avg is not None and current_load_avg > LOAD_THRESHOLD:
                self.current_target_intensity = self._calculate_safety_scaled_intensity(current_load_avg, normal_intensity)
                # Log when forced high slot gets intensity reduced for visibility
                if self.current_target_intensity < normal_intensity:
                    logger.debug(f"P95 controller: forced high slot intensity scaled down from {normal_intensity:.1f}% to {self.current_target_intensity:.1f}% due to load {current_load_avg:.2f}")
            logger.info(f"P95 controller: forced high slot (consecutive_skipped={self.consecutive_skipped_slots}, hours_since_high={time_since_high_slot/3600:.1f})")
        elif current_exceedance < exceedance_target:
            self.current_slot_is_high = True
            self.current_target_intensity = self.get_target_intensity()
        else:
            self.current_slot_is_high = False
            self.current_target_intensity = CPU_P95_BASELINE_INTENSITY

        # Reset counters when running a high slot
        if self.current_slot_is_high:
            self.consecutive_skipped_slots = 0
            self.last_high_slot_time = now

    def _calculate_current_exceedance(self):
        """
        Calculate current exceedance as ratio (0.0-1.0) from slot history.

        Returns:
            float: Exceedance ratio (0.0 = 0%, 1.0 = 100%)
        """
        with self._lock:
            if self.slots_recorded == 0:
                return 0.0
            high_slots = sum(self.slot_history[:self.slots_recorded])
            return high_slots / self.slots_recorded

    def get_current_exceedance(self):
        """Get current exceedance percentage from slot history"""
        return self._calculate_current_exceedance() * 100.0

    def get_status(self):
        """Get controller status for telemetry"""
        with self._lock:
            cpu_p95 = self.get_cpu_p95()  # get_cpu_p95() will acquire lock too (re-entrant)
            current_exceedance = self.get_current_exceedance()

            # Current slot status
            time_in_slot = time.monotonic() - self.current_slot_start if self.current_slot_start else 0
            slot_remaining = max(0, CPU_P95_SLOT_DURATION - time_in_slot)

            # High-load fallback status
            time_since_high_slot = time.monotonic() - self.last_high_slot_time
            fallback_risk = (self.consecutive_skipped_slots >= self.MAX_CONSECUTIVE_SKIPPED_SLOTS or
                            time_since_high_slot >= self.MIN_HIGH_SLOT_INTERVAL_SEC)

            return {
            'state': self.state,
            'cpu_p95': cpu_p95,
            'target_range': f"{CPU_P95_TARGET_MIN:.1f}-{CPU_P95_TARGET_MAX:.1f}%",
            'exceedance_pct': current_exceedance,
            'exceedance_target': self.get_exceedance_target(),
            'current_slot_is_high': self.current_slot_is_high,
            'slot_remaining_sec': slot_remaining,
            'slots_recorded': self.slots_recorded,
            'slots_skipped_safety': self.slots_skipped_safety,
            'consecutive_skipped_slots': self.consecutive_skipped_slots,
            'hours_since_high_slot': time_since_high_slot / 3600,
            'fallback_risk': fallback_risk,
            'target_intensity': self.current_target_intensity
        }

    def mark_current_slot_low(self):
        """
        Mark the current slot as low intensity for accurate exceedance tracking.

        This method should be called when the main loop overrides the controller's
        decision to run a high slot (e.g., due to global load safety constraints).
        It ensures that slot history accurately reflects what was actually executed.
        """
        with self._lock:
            if self.current_slot_is_high:
                self.current_slot_is_high = False
                self.current_target_intensity = CPU_P95_BASELINE_INTENSITY

    def _calculate_safety_scaled_intensity(self, current_load_avg, normal_intensity):
        """
        Calculate proportionally scaled intensity based on system load level.

        Instead of binary baseline/high switching, this provides gradual scaling:
        - Below SAFETY_SCALE_START (0.5): No scaling, normal behavior
        - Between SAFETY_SCALE_START and SAFETY_SCALE_FULL: Proportional scaling
        - Above SAFETY_SCALE_FULL (0.8): Full baseline intensity

        This reduces system impact more gracefully while maintaining some CPU activity.
        """
        if not self.SAFETY_PROPORTIONAL_ENABLED:
            # Fall back to binary baseline behavior
            return CPU_P95_BASELINE_INTENSITY

        if current_load_avg <= self.SAFETY_SCALE_START:
            # Low load - no safety scaling needed
            return normal_intensity
        elif current_load_avg >= self.SAFETY_SCALE_FULL:
            # Very high load - use full baseline
            return CPU_P95_BASELINE_INTENSITY
        else:
            # Proportional scaling between normal and baseline
            # Load range: SAFETY_SCALE_START to SAFETY_SCALE_FULL
            load_range = self.SAFETY_SCALE_FULL - self.SAFETY_SCALE_START
            load_excess = current_load_avg - self.SAFETY_SCALE_START
            scale_progress = load_excess / load_range  # 0.0 to 1.0

            # Scale from normal intensity down to minimum scaled intensity
            # At scale_progress=0: use normal intensity
            # At scale_progress=1: use minimum scaled intensity (e.g., 70% of normal)
            intensity_range = normal_intensity - (normal_intensity * self.SAFETY_MIN_INTENSITY_SCALE)
            scaled_intensity = normal_intensity - (intensity_range * scale_progress)

            # Ensure we never go below baseline or above high intensity
            return max(CPU_P95_BASELINE_INTENSITY, min(CPU_P95_HIGH_INTENSITY, scaled_intensity))

# ---------------------------
# Helpers: CPU & memory read
# ---------------------------
def read_proc_stat():
    """Read CPU statistics from /proc/stat.

    Returns:
        tuple: (total_time, idle_time) in jiffies
    """
    with open("/proc/stat", "r") as f:
        line = f.readline()
    if not line.startswith("cpu "):
        raise RuntimeError("Unexpected /proc/stat format")
    parts = line.split()
    vals = [float(x) for x in parts[1:11]]
    idle = vals[3] + vals[4]  # idle + iowait
    nonidle = vals[0] + vals[1] + vals[2] + vals[5] + vals[6] + vals[7]
    total = idle + nonidle
    return total, idle

def cpu_percent_over(dt, prev=None):
    """Calculate CPU utilization percentage over a time period.

    Args:
        dt: Time delta in seconds
        prev: Previous CPU statistics tuple, or None to read current

    Returns:
        float: CPU utilization percentage (0.0-100.0)
    """
    if prev is None:
        prev = read_proc_stat()
        time.sleep(dt)
    else:
        time.sleep(dt)
    cur = read_proc_stat()
    totald = cur[0] - prev[0]
    idled = cur[1] - prev[1]
    if totald <= 0:
        return 0.0, cur
    usage = max(0.0, 100.0 * (totald - idled) / totald)
    return usage, cur

def read_meminfo() -> Tuple[int, int, float, int, float]:
    """
    Read memory usage from /proc/meminfo using industry standards.

    Requires Linux 3.14+ (MemAvailable field). Uses industry-standard calculation
    that excludes cache/buffers for accurate utilization measurement, aligning with
    AWS CloudWatch, Azure Monitor, and Oracle's VM reclamation criteria.

    Returns:
        tuple: (total_bytes, free_bytes, used_pct_excl_cache, used_bytes_excl_cache, used_pct_incl_cache)
               - total_bytes: Total system memory in bytes
               - free_bytes: Free memory in bytes (MemFree)
               - used_pct_excl_cache: Memory utilization percentage excluding cache/buffers (Oracle-compliant)
               - used_bytes_no_cache: Used memory in bytes excluding cache/buffers
               - used_pct_incl_cache: Memory utilization percentage including cache/buffers (for comparison)

    Raises:
        RuntimeError: If /proc/meminfo is not readable, MemAvailable is missing,
                      or MemTotal is zero/missing (requires Linux 3.14+)
    """
    try:
        m = {}
        with open("/proc/meminfo") as f:
            for line in f:
                try:
                    k, v = line.split(":", 1)
                    parts = v.strip().split()
                    if parts:
                        m[k] = int(parts[0])  # in kB
                except (ValueError, IndexError):
                    # Skip malformed lines
                    continue
    except (FileNotFoundError, PermissionError, OSError) as e:
        raise RuntimeError(f"Could not read /proc/meminfo: {e}")

    total = m.get("MemTotal", 0)

    if total <= 0:
        raise RuntimeError("MemTotal not found or is zero in /proc/meminfo")

    free = m.get("MemFree", 0)
    mem_available = m.get("MemAvailable")

    if mem_available is None:
        raise RuntimeError("MemAvailable not found in /proc/meminfo (requires Linux 3.14+)")

    # ORACLE COMPLIANCE CRITICAL: Memory calculation methodology
    # Oracle's reclamation algorithm likely follows industry standard (AWS CloudWatch, Azure Monitor)
    # which excludes cache/buffers from utilization calculations. This approach ensures our
    # memory measurements align with Oracle's internal monitoring for the 20% rule.
    if mem_available >= 0 and total > 0:
        # PREFERRED METHOD: MemAvailable (Linux 3.14+) - most accurate
        # This field represents memory actually available to applications without swapping,
        # accounting for reclaimable cache/buffers. Matches Oracle's likely implementation.
        used_pct_excl_cache = (100.0 * (1.0 - mem_available / total))
        used_bytes_no_cache = (total - mem_available) * 1024
    else:
        # FALLBACK METHOD: Manual calculation for older kernels
        buffers = m.get("Buffers", 0)
        cached = m.get("Cached", 0)
        srecl = m.get("SReclaimable", 0)
        shmem = m.get("Shmem", 0)
        buff_cache = buffers + max(0, cached + srecl - shmem)
        used_no_cache_kb = max(0, total - free - buff_cache)
        used_pct_excl_cache = (100.0 * used_no_cache_kb / total) if total > 0 else 0.0
        used_bytes_no_cache = used_no_cache_kb * 1024

    # Also calculate including cache/buffers for comparison/debugging
    used_incl_cache = max(0, total - free)
    used_pct_incl_cache = (100.0 * used_incl_cache / total) if total > 0 else 0.0

    # Handle corrupt data - clamp to valid range for robustness
    if mem_available > total:
        used_pct_excl_cache = 0.0
        used_bytes_no_cache = 0
    else:
        used_pct_excl_cache = max(0.0, min(100.0, used_pct_excl_cache))
        used_bytes_no_cache = max(0, used_bytes_no_cache)

    return (total * 1024, free * 1024, used_pct_excl_cache, used_bytes_no_cache, used_pct_incl_cache)

def read_loadavg():
    """Read system load averages from /proc/loadavg.

    Returns:
        tuple: (load_1min, load_5min, load_15min, per_core_load)
               - load_1min: 1-minute load average
               - load_5min: 5-minute load average
               - load_15min: 15-minute load average
               - per_core_load: 1-minute load normalized per CPU core
    """
    # Read system load averages and return per-core load for 1-min average
    try:
        with open("/proc/loadavg", "r") as f:
            line = f.readline().strip()
        parts = line.split()
        if len(parts) >= 3:
            load_1min = float(parts[0])
            load_5min = float(parts[1]) 
            load_15min = float(parts[2])
            # Use actual system CPU count since load averages are system-wide metrics
            # that include all processes, not just loadshaper's worker threads
            cpu_count = os.cpu_count() or 1
            per_core_load = load_1min / cpu_count if cpu_count > 0 else load_1min
            return load_1min, load_5min, load_15min, per_core_load
    except Exception:
        pass
    return 0.0, 0.0, 0.0, 0.0

# ---------------------------
# Moving average (EMA)
# ---------------------------
class EMA:
    """Exponential Moving Average calculator."""

    def __init__(self, period_sec, step_sec, init=None):
        """Initialize EMA with given period and step size.

        Args:
            period_sec: Time period for smoothing in seconds
            step_sec: Update interval in seconds
            init: Initial value, or None to use first update
        """
        n = max(1.0, period_sec / max(0.1, step_sec))
        self.alpha = 2.0 / (n + 1.0)
        self.val = None if init is None else float(init)
    def update(self, x):
        """Update EMA with new value.

        Args:
            x: New value to incorporate

        Returns:
            float: Updated EMA value
        """
        x = float(x)
        if not isfinite(x):
            return self.val
        if self.val is None:
            self.val = x
        else:
            self.val = self.val + self.alpha * (x - self.val)
        return self.val

# ---------------------------
# 7-day metrics storage
# ---------------------------
class MetricsStorage:
    def __init__(self, db_path=None):
        """Initialize metrics storage with SQLite database.

        Args:
            db_path: Path to SQLite database file. If None, uses /var/lib/loadshaper/metrics.db

        Creates database schema for 7-day metrics storage with thread-safe access.
        Requires persistent storage to maintain Oracle compliance.
        """
        if db_path is None:
            db_path = os.path.join(CPUP95Controller.PERSISTENT_STORAGE_PATH, "metrics.db")

        # Validate persistent storage directory for 7-day P95 calculations
        db_dir = os.path.dirname(db_path)
        if not os.path.isdir(db_dir):
            raise FileNotFoundError(f"Metrics directory does not exist: {db_dir}. "
                                    f"A persistent volume must be mounted.")
        if not os.access(db_dir, os.W_OK):
            raise PermissionError(f"Cannot write to metrics directory: {db_dir}. "
                                  f"Check volume permissions for persistent storage.")

        self.db_path = db_path
        self.lock = threading.Lock()

        # Storage degradation tracking
        self.consecutive_failures = 0
        self.max_consecutive_failures = 5  # Mark as degraded after 5 failures
        self.last_failure_time = None

        logger.info(f"Metrics database initialized at: {self.db_path}")
        self._init_db()
    
    def _init_db(self):
        """Initialize database schema for persistent storage.

        Creates the metrics table if it doesn't exist. Fails fast if database
        cannot be created, as persistent storage is required for Oracle compliance.
        """
        with self.lock:
            try:
                with sqlite3.connect(self.db_path, timeout=10) as conn:
                    # Enable WAL mode for better concurrency
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA synchronous=NORMAL")
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS metrics (
                            timestamp REAL PRIMARY KEY,
                            cpu_pct REAL,
                            mem_pct REAL,
                            net_pct REAL,
                            load_avg REAL
                        )
                    """)
                    conn.commit()
                logger.info(f"Metrics database schema initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize metrics database at {self.db_path}: {e}")
                raise RuntimeError(f"Cannot create metrics database. "
                                   f"LoadShaper requires persistent storage for 7-day P95 calculations.")
    
    def store_sample(self, cpu_pct, mem_pct, net_pct, load_avg):
        """Store a metrics sample in the database.

        Args:
            cpu_pct: CPU utilization percentage
            mem_pct: Memory utilization percentage
            net_pct: Network utilization percentage
            load_avg: System load average

        Returns:
            bool: True if stored successfully, False otherwise
        """
        with self.lock:
            try:
                with sqlite3.connect(self.db_path, timeout=10) as conn:
                    timestamp = time.time()
                    conn.execute(
                        "INSERT OR REPLACE INTO metrics (timestamp, cpu_pct, mem_pct, net_pct, load_avg) VALUES (?, ?, ?, ?, ?)",
                        (timestamp, cpu_pct, mem_pct, net_pct, load_avg)
                    )
                    conn.commit()

                # Reset failure counter on success
                self.consecutive_failures = 0
                return True
            except Exception as e:
                logger.error(f"Failed to store sample: {e}")

                # Track consecutive failures for degradation detection
                self.consecutive_failures += 1
                self.last_failure_time = time.time()

                if self.consecutive_failures >= self.max_consecutive_failures:
                    logger.warning(f"Storage degraded: {self.consecutive_failures} consecutive failures")

                return False
    
    def get_percentile(self, metric_name, percentile=95.0, days_back=7):
        """Calculate percentile for a metric over the specified time period.

        Args:
            metric_name: Metric name ('cpu', 'mem', 'net', 'load')
            percentile: Percentile to calculate (0-100)
            days_back: Number of days of data to analyze

        Returns:
            float: Calculated percentile value, or None if insufficient data
        """
        
        column_map = {
            'cpu': 'cpu_pct',
            'mem': 'mem_pct', 
            'net': 'net_pct',
            'load': 'load_avg'
        }
        
        if metric_name not in column_map:
            return None
        
        column = column_map[metric_name]
        cutoff_time = time.time() - (days_back * 24 * 3600)
        
        with self.lock:
            try:
                with sqlite3.connect(self.db_path, timeout=10) as conn:
                    cursor = conn.execute(
                        f"SELECT {column} FROM metrics WHERE timestamp >= ? AND {column} IS NOT NULL ORDER BY {column}",
                        (cutoff_time,)
                    )
                    values = [row[0] for row in cursor.fetchall()]

                if not values:
                    return None

                # Calculate percentile manually (no numpy dependency)
                index = (percentile / 100.0) * (len(values) - 1)
                if index == int(index):
                    return values[int(index)]
                else:
                    lower = values[int(index)]
                    upper = values[int(index) + 1]
                    return lower + (upper - lower) * (index - int(index))

            except Exception as e:
                logger.error(f"Failed to get percentile: {e}")
                return None
    
    def cleanup_old(self, days_to_keep=7):
        """Remove old metrics data from database.

        Args:
            days_to_keep: Number of days of data to retain (default: 7)

        Returns:
            int: Number of records deleted, or 0 if database unavailable
        """

        cutoff_time = time.time() - (days_to_keep * 24 * 3600)
        
        with self.lock:
            try:
                with sqlite3.connect(self.db_path, timeout=10) as conn:
                    cursor = conn.execute("DELETE FROM metrics WHERE timestamp < ?", (cutoff_time,))
                    deleted = cursor.rowcount
                    conn.commit()
                return deleted
            except Exception as e:
                logger.error(f"Failed to cleanup old data: {e}")
                return 0
    
    def get_sample_count(self, days_back=7):
        """Get count of metrics samples within specified time period.

        Args:
            days_back: Number of days to look back (default: 7)

        Returns:
            int: Number of samples found, or 0 if database unavailable/error
        """

        cutoff_time = time.time() - (days_back * 24 * 3600)
        
        with self.lock:
            try:
                with sqlite3.connect(self.db_path, timeout=10) as conn:
                    cursor = conn.execute("SELECT COUNT(*) FROM metrics WHERE timestamp >= ?", (cutoff_time,))
                    count = cursor.fetchone()[0]
                return count
            except Exception as e:
                logger.error(f"Failed to get sample count: {e}")
                return 0

    def is_storage_degraded(self):
        """Check if storage is in a degraded state due to consecutive failures.

        Returns:
            bool: True if storage is degraded, False otherwise
        """
        return self.consecutive_failures >= self.max_consecutive_failures

    def get_storage_status(self):
        """Get detailed storage status for telemetry.

        Returns:
            dict: Storage status information
        """
        return {
            'consecutive_failures': self.consecutive_failures,
            'is_degraded': self.is_storage_degraded(),
            'last_failure_time': self.last_failure_time,
            'max_consecutive_failures': self.max_consecutive_failures
        }

# ---------------------------
# CPU workers (busy/sleep)
# ---------------------------
def cpu_worker(shared_duty: Value, stop_flag: Value):
    """
    Lightweight CPU load generator designed for minimal system impact.
    
    Key design principles for minimal responsiveness impact:
    - Runs at lowest OS priority (nice 19) to immediately yield to real workloads
    - Uses simple arithmetic operations to minimize cache pollution and context switching overhead
    - Short work periods (100ms max) with frequent yield opportunities
    - Always includes sleep slice (5ms minimum) to ensure scheduler can run other processes
    - Immediately responds to stop_flag when system load indicates contention
    """
    os.nice(19)  # lowest priority; always yield to real workloads
    TICK = 0.1   # 100ms work periods - short enough to be responsive
    junk = 1.0   # Simple arithmetic to minimize cache/memory pressure
    
    while True:
        if stop_flag.value == 1.0:
            time.sleep(SLEEP_SLICE)  # Still yield CPU when paused
            continue
            
        d = float(shared_duty.value)
        d = 0.0 if d < 0 else (MAX_DUTY if d > MAX_DUTY else d)
        busy = d * TICK  # Calculate active work time within this tick
        
        # CPU-intensive work period (simple arithmetic chosen for minimal system impact)
        start = time.perf_counter()
        while (time.perf_counter() - start) < busy:
            junk = junk * 1.0000001 + 1.0  # Lightweight arithmetic, avoids memory allocation
            
        # Always yield remaining time in tick, minimum 5ms for scheduler responsiveness
        rest = TICK - busy
        if rest > 0:
            time.sleep(rest)
        else:
            time.sleep(SLEEP_SLICE)  # Minimum yield to ensure other processes can run

# ---------------------------
# RAM allocator & toucher
# ---------------------------
mem_lock = threading.Lock()
mem_block = bytearray(0)

def set_mem_target_bytes(target_bytes):
    """
    Set target memory occupation in bytes.
    
    Gradually increases or decreases the allocated memory block to reach
    the target size, with step limits to prevent rapid allocation/deallocation.
    Calls garbage collection after shrinking to help return memory to OS.
    
    Args:
        target_bytes (int): Desired memory allocation size in bytes
    """
    import gc
    
    with mem_lock:
        cur = len(mem_block)
        step = MEM_STEP_MB * 1024 * 1024
        if target_bytes < 0:
            target_bytes = 0
        if target_bytes > cur:
            # Grow memory allocation
            inc = min(step, target_bytes - cur)
            mem_block.extend(b"\x00" * inc)
        elif target_bytes < cur:
            # Shrink memory allocation
            dec = min(step, cur - target_bytes)
            del mem_block[cur - dec:cur]
            # Help return memory to OS (especially effective with musl libc)
            gc.collect()

def mem_nurse_thread(stop_evt: threading.Event):
    """
    Memory occupation maintenance thread.
    
    Periodically touches allocated memory pages to keep them resident in RAM,
    ensuring they count toward memory utilization metrics. Uses system page
    size for efficient touching and respects load thresholds.
    """
    
    # Use system page size for portable and efficient memory touching
    try:
        PAGE = os.getpagesize()
    except AttributeError:
        # Fallback for systems where getpagesize() is not available (e.g., macOS)
        PAGE = 4096
    
    while not stop_evt.is_set():
        # Pause memory touching when load threshold exceeded (like other workers)
        if LOAD_CHECK_ENABLED and paused.value:
            time.sleep(MEM_TOUCH_INTERVAL_SEC)
            continue
            
        with mem_lock:
            size = len(mem_block)
            if size > 0:
                # Touch one byte per page to keep pages resident
                for pos in range(0, size, PAGE):
                    mem_block[pos] = (mem_block[pos] + 1) & 0xFF
        
        time.sleep(MEM_TOUCH_INTERVAL_SEC)

# ---------------------------
# NIC sensing helpers
# ---------------------------
def read_host_nic_bytes(iface: str):
    """Read network interface byte counters from host filesystem.

    Args:
        iface: Network interface name (e.g. 'eth0')

    Returns:
        tuple: (tx_bytes, rx_bytes) or None if not available
    """
    # Requires a bind-mount of /sys/class/net -> /host_sys_class_net
    base = f"/host_sys_class_net/{iface}/statistics"
    try:
        with open(f"{base}/tx_bytes", "r") as f:
            tx = int(f.read().strip())
        with open(f"{base}/rx_bytes", "r") as f:
            rx = int(f.read().strip())
        return tx, rx
    except Exception:
        return None

def read_container_nic_bytes(iface: str):
    """Read network interface byte counters from /proc/net/dev.

    Args:
        iface: Network interface name (e.g. 'eth0')

    Returns:
        tuple: (tx_bytes, rx_bytes) or None if not found
    """
    # Parse /proc/net/dev (available in all containers)
    try:
        with open("/proc/net/dev", "r") as f:
            for line in f:
                if ":" not in line:
                    continue
                name, rest = [x.strip() for x in line.split(":", 1)]
                if name == iface:
                    parts = rest.split()
                    rx = int(parts[0])   # bytes
                    tx = int(parts[8])   # bytes
                    return (tx, rx)
    except Exception:
        pass
    return None

def read_host_nic_speed_mbit(iface: str):
    """Read network interface speed from host system.

    Args:
        iface: Network interface name (e.g., 'eth0')

    Returns:
        float: Interface speed in Mbit/s, or NET_LINK_MBIT if unable to read
    """
    try:
        with open(f"/host_sys_class_net/{iface}/speed", "r") as f:
            sp = float(f.read().strip())
        if sp > 0:
            return sp
    except Exception:
        pass
    return NET_LINK_MBIT

def nic_utilization_pct(prev, cur, dt_sec, link_mbit):
    """Calculate network interface utilization percentage.

    Args:
        prev: Previous (tx_bytes, rx_bytes) reading
        cur: Current (tx_bytes, rx_bytes) reading
        dt_sec: Time delta in seconds
        link_mbit: Link capacity in megabits per second

    Returns:
        float: Network utilization percentage (0.0-100.0)
    """
    if prev is None or cur is None or dt_sec <= 0 or link_mbit <= 0:
        return None
    dtx = max(0, cur[0] - prev[0])
    drx = max(0, cur[1] - prev[1])
    bits = (dtx + drx) * 8.0
    bps = bits / dt_sec
    cap_bps = link_mbit * 1_000_000.0
    util = 100.0 * (bps / cap_bps) if cap_bps > 0 else 0.0
    if util < 0:
        util = 0.0
    return util

# ---------------------------
# Native network generator
# ---------------------------

class TokenBucket:
    """
    Token bucket rate limiter with 5ms precision for smooth traffic generation.

    Implements a classic token bucket algorithm with elapsed-time based accumulation
    to prevent dead zones at very low rates. Supports both UDP and TCP traffic
    generation with configurable burst sizes.
    """

    def __init__(self, rate_mbps: float):
        """
        Initialize token bucket with specified rate.

        Args:
            rate_mbps: Target rate in megabits per second
        """
        self.rate_mbps = max(0.001, rate_mbps)  # Minimum rate to prevent division by zero
        self.capacity_bits = max(1000, self.rate_mbps * 1_000_000 * 0.1)  # 100ms burst capacity
        self.tokens = self.capacity_bits
        self.last_update = time.time()
        self.tick_interval = 0.005  # 5ms precision

    def update_rate(self, new_rate_mbps: float):
        """Update bucket rate and recalculate capacity."""
        self.rate_mbps = max(0.001, new_rate_mbps)
        self.capacity_bits = max(1000, self.rate_mbps * 1_000_000 * 0.1)
        # Clamp current tokens to new capacity
        self.tokens = min(self.tokens, self.capacity_bits)

    def can_send(self, packet_size_bytes: int) -> bool:
        """
        Check if packet can be sent based on available tokens.

        Args:
            packet_size_bytes: Size of packet to send in bytes

        Returns:
            bool: True if packet can be sent immediately
        """
        packet_bits = packet_size_bytes * 8
        self._add_tokens()
        return self.tokens >= packet_bits

    def consume(self, packet_size_bytes: int) -> bool:
        """
        Consume tokens for sending a packet.

        Args:
            packet_size_bytes: Size of packet being sent in bytes

        Returns:
            bool: True if tokens were consumed, False if insufficient tokens
        """
        packet_bits = packet_size_bytes * 8
        self._add_tokens()

        if self.tokens >= packet_bits:
            self.tokens -= packet_bits
            return True
        return False

    def wait_time(self, packet_size_bytes: int) -> float:
        """
        Calculate time to wait before packet can be sent.

        Args:
            packet_size_bytes: Size of packet to send in bytes

        Returns:
            float: Time to wait in seconds (0 if can send immediately)
        """
        packet_bits = packet_size_bytes * 8
        self._add_tokens()

        if self.tokens >= packet_bits:
            return 0.0

        needed_bits = packet_bits - self.tokens
        wait_seconds = needed_bits / (self.rate_mbps * 1_000_000)
        return max(0.0, wait_seconds)

    def _add_tokens(self):
        """Add tokens based on elapsed time since last update."""
        now = time.time()
        elapsed = now - self.last_update

        # Optimization: Only update tokens if enough time has passed
        # This reduces overhead for high-frequency calls
        if elapsed >= self.tick_interval:
            tokens_to_add = elapsed * self.rate_mbps * 1_000_000
            self.tokens = min(self.capacity_bits, self.tokens + tokens_to_add)
            self.last_update = now


class NetworkGenerator:
    """
    Enhanced network traffic generator with state machine and peer validation.

    Implements reliable network generation with automatic fallback mechanisms,
    peer validation, tx_bytes monitoring, and comprehensive health scoring.
    Designed for Oracle Cloud VM protection with robust external traffic validation.
    """

    # Default public DNS servers for reliable external traffic
    DEFAULT_DNS_SERVERS = ["8.8.8.8", "1.1.1.1", "9.9.9.9"]
    RFC2544_ADDRESSES = ["198.18.0.1", "198.19.255.254"]  # Fallback benchmarking addresses

    # PEER REPUTATION SYSTEM CONSTANTS
    # Reputation range: 0.0 (blacklisted) to 100.0 (perfect peer)
    REPUTATION_INITIAL_NEUTRAL = 50.0     # New peers start with neutral reputation
    REPUTATION_INITIAL_DNS = 60.0         # DNS servers get higher initial trust
    REPUTATION_INITIAL_HIGH = 80.0        # Fallback DNS servers get premium trust
    REPUTATION_INITIAL_LOCAL = 30.0       # Local fallback gets low trust (non-protective)
    REPUTATION_VALIDATION_BOOST = 10.0    # Bonus for passing initial validation
    REPUTATION_VALIDATION_PENALTY = 20.0  # Penalty for failing initial validation
    REPUTATION_SUCCESS_INCREMENT = 1.0    # Small bonus for each successful send
    REPUTATION_FAILURE_PENALTY = 5.0     # Moderate penalty for each failed send
    REPUTATION_RECOVERY_BOOST = 15.0      # Large bonus for peer recovery
    REPUTATION_BLACKLIST_THRESHOLD = 20.0 # Below this triggers temporary blacklist
    REPUTATION_RECOVERY_MINIMUM = 20.0    # Minimum reputation after recovery boost
    REPUTATION_MAX = 100.0               # Maximum possible reputation
    REPUTATION_MIN = 0.0                 # Minimum possible reputation

    # BLACKLIST AND RECOVERY TIMING CONSTANTS
    BLACKLIST_DURATION_SEC = 120.0       # 2 minutes blacklist for failed peers
    RECOVERY_CHECK_INTERVAL_SEC = 60.0   # Check every minute for peer recovery

    # NETWORK VALIDATION TIMEOUTS (in seconds)
    DNS_VALIDATION_TIMEOUT = 0.5         # 500ms for DNS query validation
    TCP_VALIDATION_TIMEOUT = 0.3         # 300ms for TCP handshake validation

    # HEALTH SCORING SYSTEM CONSTANTS
    # Health score range: 0 (completely failed) to 100 (perfect health)
    HEALTH_SCORE_ACTIVE_UDP = 100        # Perfect score for active UDP state
    HEALTH_SCORE_ACTIVE_TCP = 75         # Good score for active TCP state
    HEALTH_SCORE_DEGRADED_LOCAL = 30     # Poor score for local-only generation
    HEALTH_SCORE_VALIDATING = 50         # Moderate score during validation
    HEALTH_SCORE_INITIALIZING = 40       # Lower score during initialization
    HEALTH_SCORE_ERROR_OFF = 0           # Failed score for error/off states

    # Health scoring weights for composite calculation (must sum to 100%)
    HEALTH_WEIGHT_SEND_SUCCESS = 40      # 40% weight for send success rate
    HEALTH_WEIGHT_TX_BYTES = 40          # 40% weight for tx_bytes verification
    HEALTH_WEIGHT_PEER_AVAILABILITY = 10 # 10% weight for peer availability
    HEALTH_WEIGHT_EXTERNAL_VERIFICATION = 10  # 10% weight for external egress verification

    # DNS RATE LIMITING
    DNS_QPS_MAX = 10.0                   # Maximum 10 DNS queries per second

    # CPU YIELD AND PERFORMANCE CONSTANTS
    CPU_YIELD_INTERVAL = 100             # Yield CPU every 100 packet sends
    CPU_YIELD_DURATION = 0.0001          # 0.1ms yield duration
    TOKEN_BUCKET_MAX_WAIT_SEC = 0.010    # Maximum sleep time for token bucket (10ms)

    def __init__(self, rate_mbps: float, protocol: str = "udp", ttl: int = 1,
                 packet_size: int = 1100, port: int = 15201, timeout: float = 0.5,
                 require_external: bool = False, validate_startup: bool = True):
        """
        Initialize enhanced network generator.

        Args:
            rate_mbps: Target rate in megabits per second
            protocol: 'udp' or 'tcp'
            ttl: IP Time-to-Live (1 = first hop only for safety)
            packet_size: Packet payload size in bytes
            port: Target port number
            timeout: Connection timeout in seconds
            require_external: Require external (non-RFC1918) addresses for E2 compliance
            validate_startup: Validate peer connectivity at startup
        """
        # Core networking
        self.bucket = TokenBucket(rate_mbps)
        self.protocol = protocol.lower()
        self.ttl = max(1, ttl)
        self.packet_size = max(64, min(65507, packet_size))
        self.port = max(1024, min(65535, port))
        self.timeout = max(0.1, timeout)
        self.require_external = require_external
        self.validate_startup = validate_startup

        # State machine
        self.state = NetworkState.OFF
        self.state_start_time = time.monotonic()
        self.state_transitions = []  # History of state changes

        # Peer management
        self.peers = {}  # {address: PeerInfo}
        self.current_peer_index = 0
        self.fallback_dns_servers = self.DEFAULT_DNS_SERVERS.copy()
        self.local_fallback = "127.0.0.1"

        # Connection management
        self.socket = None
        self.tcp_connections = {}
        self.resolved_targets = {}

        # Validation and monitoring
        self.tx_bytes_ema = 0.0
        self.tx_bytes_alpha = 0.2  # EMA smoothing factor
        self.last_tx_bytes = None
        self.network_interface = None
        self.validation_failures = 0
        self.external_egress_verified = False

        # Health scoring
        self.health_score = 0
        self.send_success_rate = 0.0
        self.recent_send_attempts = []
        self.peer_availability = 0.0
        self.recent_errors = []

        # Timing and hysteresis
        self.state_debounce_sec = 5.0
        self.state_min_on_sec = 15.0
        self.state_min_off_sec = 20.0
        self.last_transition_time = time.monotonic()

        # DNS generation settings
        self.dns_packet_size = min(packet_size, 1100)
        self.dns_qps_max = self.DNS_QPS_MAX
        self.last_dns_send = 0.0

        # Initialize packet data
        self._prepare_packet_data()

        # Pre-allocate socket buffers for efficiency
        self.send_buffer_size = max(1024 * 1024, self.packet_size * 10)

    def _prepare_packet_data(self):
        """Pre-allocate packet data for zero-copy sending."""
        timestamp = struct.pack('!d', time.time())
        sequence_pattern = b'LoadShaper-' + (b'x' * (self.packet_size - len(timestamp) - 11))
        self.packet_data = timestamp + sequence_pattern[:self.packet_size - len(timestamp)]

    def start(self, target_addresses: list):
        """
        Start network generation with state machine validation.

        Args:
            target_addresses: List of target IP addresses/hostnames
        """
        self._transition_state(NetworkState.INITIALIZING, "start() called")

        try:
            # Initialize peer list
            if not target_addresses:
                logger.info("No peers provided, using default DNS servers for external traffic")
                target_addresses = self.DEFAULT_DNS_SERVERS.copy()

            # Validate external address requirement
            if self.require_external:
                for addr in target_addresses:
                    if not self._is_address_external(addr):
                        error_msg = f"E2 shape requires external peers, got internal address: {addr}"
                        logger.error(error_msg)
                        self._transition_state(NetworkState.ERROR, error_msg)
                        return

            # Initialize peers
            self._initialize_peers(target_addresses)

            # Detect network interface for tx_bytes monitoring
            self._detect_network_interface()

            if self.validate_startup:
                self._transition_state(NetworkState.VALIDATING, "validating peers")
                self._validate_all_peers()

            # Start primary protocol
            target_state = NetworkState.ACTIVE_TCP if self.protocol == "tcp" else NetworkState.ACTIVE_UDP
            self._transition_state(target_state, "validation complete")
            self._start_protocol(self.protocol)

        except Exception as e:
            error_msg = f"Failed to start network generator: {e}"
            logger.error(error_msg)
            self._transition_state(NetworkState.ERROR, error_msg)

    def _initialize_peers(self, addresses: list):
        """Initialize peer tracking structures."""
        self.peers = {}
        for addr in addresses:
            self.peers[addr] = {
                'state': PeerState.UNVALIDATED,
                'reputation': self.REPUTATION_INITIAL_NEUTRAL,
                'failures': 0,
                'successes': 0,
                'last_attempt': 0.0,
                'blacklist_until': 0.0,
                'is_external': self._is_address_external(addr)
            }

    def _detect_network_interface(self):
        """Detect primary network interface for tx_bytes monitoring."""
        try:
            # Try common interface names
            for interface in ['eth0', 'ens5', 'enp0s3', 'wlan0']:
                if read_nic_tx_bytes(interface) is not None:
                    self.network_interface = interface
                    logger.debug(f"Using network interface {interface} for tx_bytes monitoring")
                    return

            logger.warning("Could not detect network interface for tx_bytes monitoring")
        except Exception as e:
            logger.warning(f"Failed to detect network interface: {e}")

    def _is_address_external(self, address: str) -> bool:
        """Check if address is external using DNS resolution if needed."""
        try:
            # Try to parse as IP address first
            if is_external_address(address):
                return True

            # If not a valid IP, try DNS resolution
            addr_info = socket.getaddrinfo(address, 53, socket.AF_UNSPEC, socket.SOCK_DGRAM)
            for family, sock_type, proto, canonname, sockaddr in addr_info:
                ip = sockaddr[0]
                if is_external_address(ip):
                    return True

            return False
        except Exception:
            return False

    def _validate_all_peers(self):
        """Validate connectivity to all peers."""
        valid_peers = 0

        for address, peer_info in self.peers.items():
            if self._validate_peer(address):
                peer_info['state'] = PeerState.VALID
                peer_info['reputation'] += self.REPUTATION_VALIDATION_BOOST
                valid_peers += 1
            else:
                peer_info['state'] = PeerState.INVALID
                peer_info['reputation'] -= self.REPUTATION_VALIDATION_PENALTY

        if valid_peers == 0:
            logger.warning("No valid peers found, will attempt DNS fallback")

        logger.info(f"Peer validation complete: {valid_peers}/{len(self.peers)} peers valid")

    def _validate_peer(self, address: str) -> bool:
        """Validate connectivity to a single peer with real reachability test."""
        try:
            # Check if this is a DNS server - use DNS query for validation
            if address in self.DEFAULT_DNS_SERVERS:
                return self._validate_dns_peer(address)
            else:
                # For generic peers, try TCP handshake on configured port
                return self._validate_generic_peer(address)
        except Exception as e:
            logger.debug(f"Peer validation failed for {address}: {e}")
            return False

    def _validate_dns_peer(self, dns_server: str) -> bool:
        """Validate DNS server with actual DNS query."""
        try:
            # Create DNS query packet for a simple A record lookup
            dns_query = build_dns_query("google.com", qtype=1, packet_size=512)

            # Send DNS query and wait for response
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            test_sock.settimeout(self.DNS_VALIDATION_TIMEOUT)
            try:
                test_sock.sendto(dns_query, (dns_server, 53))
                response, addr = test_sock.recvfrom(512)
                # If we got a response, DNS server is working
                return len(response) > 12  # Minimum DNS response size
            except (socket.timeout, socket.error):
                return False
            finally:
                test_sock.close()
        except Exception:
            return False

    def _validate_generic_peer(self, address: str) -> bool:
        """Validate generic peer with TCP handshake."""
        try:
            # Get address info for protocol-agnostic connection
            addr_info = socket.getaddrinfo(address, self.port, socket.AF_UNSPEC,
                                         socket.SOCK_STREAM, socket.IPPROTO_TCP)

            # Try TCP handshake on first available address
            for family, socktype, proto, canonname, sockaddr in addr_info:
                try:
                    test_sock = socket.socket(family, socktype, proto)
                    test_sock.settimeout(self.TCP_VALIDATION_TIMEOUT)
                    test_sock.connect(sockaddr)
                    test_sock.close()
                    return True
                except (socket.timeout, socket.error, OSError):
                    if 'test_sock' in locals():
                        test_sock.close()
                    continue

            return False
        except Exception:
            return False

    def _check_peer_recovery(self):
        """Periodically attempt to recover blacklisted peers."""
        current_time = time.time()

        # Only check for recovery every 60 seconds to avoid excessive overhead
        if not hasattr(self, '_last_recovery_check'):
            self._last_recovery_check = current_time
        elif current_time - self._last_recovery_check < self.RECOVERY_CHECK_INTERVAL_SEC:
            return

        self._last_recovery_check = current_time

        # Check blacklisted peers for recovery
        for address, peer_info in self.peers.items():
            if (peer_info['state'] == PeerState.INVALID and
                peer_info['blacklist_until'] > 0 and
                current_time > peer_info['blacklist_until']):

                logger.debug(f"Attempting recovery for blacklisted peer {address}")

                # Reset blacklist and try validation
                peer_info['blacklist_until'] = 0.0
                if self._validate_peer(address):
                    peer_info['state'] = PeerState.VALID
                    peer_info['reputation'] = max(self.REPUTATION_RECOVERY_MINIMUM, peer_info['reputation'] + self.REPUTATION_RECOVERY_BOOST)
                    logger.info(f"Peer {address} recovered and returned to service")
                else:
                    # Failed recovery - extend blacklist with exponential backoff
                    peer_info['failures'] += 1
                    backoff_time = min(600, 120 * (2 ** min(peer_info['failures'], 5)))  # Max 10 minutes
                    peer_info['blacklist_until'] = current_time + backoff_time
                    logger.debug(f"Peer {address} failed recovery, blacklisted for {backoff_time}s")

    def _transition_state(self, new_state: NetworkState, reason: str):
        """Transition to new state with logging and hysteresis checks."""
        if new_state == self.state:
            return

        current_time = time.monotonic()
        time_in_state = current_time - self.state_start_time

        # Check debounce timing - prevent rapid state changes
        if hasattr(self, 'last_transition_time'):
            time_since_last_transition = current_time - self.last_transition_time
            if time_since_last_transition < self.state_debounce_sec:
                logger.debug(f"State transition blocked by debounce: {time_since_last_transition:.1f}s < {self.state_debounce_sec}s")
                return

        # Check hysteresis rules - minimum time in active states
        if self.state in [NetworkState.ACTIVE_UDP, NetworkState.ACTIVE_TCP]:
            if time_in_state < self.state_min_on_sec:
                logger.debug(f"State transition blocked by min-on time: {time_in_state:.1f}s < {self.state_min_on_sec}s")
                return

        # Check minimum off time for inactive states
        if self.state in [NetworkState.OFF, NetworkState.ERROR, NetworkState.DEGRADED_LOCAL]:
            if time_in_state < self.state_min_off_sec:
                logger.debug(f"State transition blocked by min-off time: {time_in_state:.1f}s < {self.state_min_off_sec}s")
                return

        # Record transition
        self.state_transitions.append({
            'from_state': self.state.value,
            'to_state': new_state.value,
            'reason': reason,
            'timestamp': current_time,
            'time_in_previous_state': time_in_state
        })

        # Keep only recent transitions
        if len(self.state_transitions) > 20:
            self.state_transitions = self.state_transitions[-20:]

        logger.info(f"Network state: {self.state.value} → {new_state.value} ({reason})")
        self.state = new_state
        self.state_start_time = current_time
        self.last_transition_time = current_time

    def _start_protocol(self, protocol: str):
        """Initialize socket for the specified protocol."""
        try:
            self.protocol = protocol.lower()

            if protocol == "udp":
                self._start_udp()
            elif protocol == "tcp":
                self._start_tcp()
            else:
                raise ValueError(f"Unsupported protocol: {protocol}")

        except Exception as e:
            logger.error(f"Failed to start {protocol}: {e}")
            self._handle_protocol_failure()

    def _start_udp(self):
        """Initialize UDP socket with improved error handling."""
        # Determine address family from first valid peer
        target_ip = self._get_next_valid_peer()
        if not target_ip:
            self._handle_no_valid_peers()
            return

        family = socket.AF_INET
        try:
            socket.inet_aton(target_ip)
        except socket.error:
            # Try IPv6
            try:
                socket.inet_pton(socket.AF_INET6, target_ip)
                family = socket.AF_INET6
            except socket.error:
                # Must be hostname, resolve it
                try:
                    addr_info = socket.getaddrinfo(target_ip, self.port, socket.AF_UNSPEC, socket.SOCK_DGRAM)
                    family = addr_info[0][0]
                except socket.gaierror as e:
                    logger.error(f"Failed to resolve {target_ip}: {e}")
                    self._handle_protocol_failure()
                    return

        self.socket = socket.socket(family, socket.SOCK_DGRAM)

        # Set TTL/hop limit for safety
        if family == socket.AF_INET:
            self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, self.ttl)
        elif family == socket.AF_INET6:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_UNICAST_HOPS, self.ttl)

        # Optimize socket
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, self.send_buffer_size)
        self.socket.setblocking(False)

    def _start_tcp(self):
        """Initialize TCP connection management."""
        self.socket = None  # TCP uses connection pool
        self.tcp_connections = {}

    def _get_next_valid_peer(self) -> Optional[str]:
        """Get next valid peer using round-robin."""
        valid_peers = [addr for addr, info in self.peers.items()
                      if info['state'] == PeerState.VALID and time.time() > info['blacklist_until']]

        if not valid_peers:
            return None

        # Round-robin through valid peers
        if self.current_peer_index >= len(valid_peers):
            self.current_peer_index = 0

        peer = valid_peers[self.current_peer_index]
        self.current_peer_index = (self.current_peer_index + 1) % len(valid_peers)
        return peer

    def _handle_no_valid_peers(self):
        """Handle situation when no valid peers are available."""
        logger.warning("No valid peers available, attempting fallback")

        # Try DNS servers as fallback
        if self._try_dns_fallback():
            return

        # Final fallback to local generation
        self._try_local_fallback()

    def _try_dns_fallback(self) -> bool:
        """Attempt to use DNS servers as fallback peers."""
        logger.info("Attempting DNS server fallback for external traffic")

        for dns_server in self.fallback_dns_servers:
            if dns_server not in self.peers:
                self.peers[dns_server] = {
                    'state': PeerState.UNVALIDATED,
                    'reputation': self.REPUTATION_INITIAL_DNS,
                    'failures': 0,
                    'successes': 0,
                    'last_attempt': 0.0,
                    'blacklist_until': 0.0,
                    'is_external': True
                }

                if self._validate_peer(dns_server):
                    self.peers[dns_server]['state'] = PeerState.VALID
                    logger.info(f"DNS fallback successful: using {dns_server}")
                    return True

        logger.warning("DNS fallback failed, all DNS servers unreachable")
        return False

    def _try_local_fallback(self):
        """Final fallback to local generation (non-protective)."""
        logger.warning("Entering degraded local generation mode - Oracle protection NOT guaranteed")

        self.peers[self.local_fallback] = {
            'state': PeerState.VALID,
            'reputation': self.REPUTATION_INITIAL_LOCAL,
            'failures': 0,
            'successes': 0,
            'last_attempt': 0.0,
            'blacklist_until': 0.0,
            'is_external': False
        }

        self._transition_state(NetworkState.DEGRADED_LOCAL, "no external peers available")

    def _handle_protocol_failure(self):
        """Handle protocol-level failures with fallback logic."""
        if self.state == NetworkState.ACTIVE_UDP:
            logger.info("UDP failed, falling back to TCP")
            self._transition_state(NetworkState.ACTIVE_TCP, "UDP protocol failure")
            self._start_protocol("tcp")
        elif self.state == NetworkState.ACTIVE_TCP:
            logger.warning("TCP also failed, attempting peer rotation")
            self._rotate_to_next_peer()
        else:
            logger.error("Protocol failure in unexpected state")
            self._transition_state(NetworkState.ERROR, "protocol failure")

    def _rotate_to_next_peer(self):
        """Rotate to next available peer or fallback."""
        next_peer = self._get_next_valid_peer()
        if next_peer:
            logger.info(f"Rotating to next peer: {next_peer}")
            target_state = NetworkState.ACTIVE_TCP if self.protocol == "tcp" else NetworkState.ACTIVE_UDP
            self._transition_state(target_state, "peer rotation")
            self._start_protocol(self.protocol)
        else:
            self._handle_no_valid_peers()

    def send_burst(self, duration_seconds: float) -> int:
        """
        Send traffic burst with validation and state management.

        Args:
            duration_seconds: How long to send traffic

        Returns:
            int: Number of packets sent
        """
        if self.state == NetworkState.OFF:
            return 0

        # Record tx_bytes before burst for validation
        tx_before = self._get_tx_bytes()

        packets_sent = 0
        start_time = time.time()
        send_attempts = 0

        while (time.time() - start_time) < duration_seconds:
            # Check if we can send a packet
            if not self.bucket.can_send(self.packet_size):
                wait_time = self.bucket.wait_time(self.packet_size)
                if wait_time > 0:
                    # Sleep for the actual wait time needed, but cap at 10ms to stay responsive
                    # This prevents busy-waiting while still maintaining reasonable burst control
                    sleep_time = min(wait_time, self.TOKEN_BUCKET_MAX_WAIT_SEC)
                    time.sleep(sleep_time)
                continue

            send_attempts += 1
            success = False

            try:
                if self.state == NetworkState.ACTIVE_UDP:
                    success = self._send_udp_burst_packet()
                elif self.state == NetworkState.ACTIVE_TCP:
                    success = self._send_tcp_burst_packet()
                elif self.state == NetworkState.DEGRADED_LOCAL:
                    success = self._send_local_packet()

                if success:
                    packets_sent += 1
                    self.bucket.consume(self.packet_size)

            except Exception as e:
                logger.debug(f"Send error in state {self.state.value}: {e}")

            # Yield CPU periodically
            if send_attempts % self.CPU_YIELD_INTERVAL == 0:
                time.sleep(self.CPU_YIELD_DURATION)

        # Validate transmission effectiveness
        self._validate_transmission_effectiveness(tx_before, packets_sent, send_attempts)

        # Check for peer recovery periodically
        self._check_peer_recovery()

        # Update health metrics
        self._update_health_metrics(packets_sent, send_attempts)

        return packets_sent

    def _send_udp_burst_packet(self) -> bool:
        """Send single UDP packet to current peer."""
        peer = self._get_next_valid_peer()
        if not peer:
            self._handle_no_valid_peers()
            return False

        try:
            # Special handling for DNS servers (port 53)
            port = 53 if peer in self.fallback_dns_servers else self.port

            if port == 53:
                # Send DNS query with EDNS0 padding
                packet = self._build_dns_packet()
                if self._should_rate_limit_dns():
                    return False
                self.last_dns_send = time.time()
            else:
                # Send regular packet
                packet = self._get_current_packet()

            self.socket.sendto(packet, (peer, port))
            self._record_peer_success(peer)
            return True

        except socket.error as e:
            self._record_peer_failure(peer, str(e))
            return False

    def _send_tcp_burst_packet(self) -> bool:
        """Send single TCP packet using connection pool."""
        peer = self._get_next_valid_peer()
        if not peer:
            self._handle_no_valid_peers()
            return False

        try:
            conn = self._get_tcp_connection(peer)
            if not conn:
                return False

            packet = self._get_current_packet()
            conn.send(packet)
            self._record_peer_success(peer)
            return True

        except (socket.error, OSError) as e:
            self._record_peer_failure(peer, str(e))
            if peer in self.tcp_connections:
                try:
                    self.tcp_connections[peer].close()
                except:
                    pass
                del self.tcp_connections[peer]
            return False

    def _send_local_packet(self) -> bool:
        """Send packet to local interface (degraded mode)."""
        try:
            if not self.socket:
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.socket.setblocking(False)

            packet = self._get_current_packet()
            self.socket.sendto(packet, (self.local_fallback, self.port))
            return True

        except socket.error:
            return False

    def _build_dns_packet(self) -> bytes:
        """Build DNS query packet for external traffic generation."""
        import secrets

        # Generate random query name to avoid caching
        random_label = secrets.token_hex(6)
        qname = f"x-{random_label}.example.com"
        qtype = 1 if random.random() > 0.5 else 28  # Alternate A and AAAA queries

        return build_dns_query(qname, qtype, self.dns_packet_size)

    def _should_rate_limit_dns(self) -> bool:
        """Check if DNS queries should be rate limited."""
        if time.time() - self.last_dns_send < (1.0 / self.dns_qps_max):
            return True
        return False

    def _get_current_packet(self) -> bytes:
        """Get packet with current timestamp."""
        current_time = struct.pack('!d', time.time())
        return current_time + self.packet_data[8:]

    def _get_tcp_connection(self, peer: str):
        """Get or create TCP connection for peer with IPv4/IPv6 support."""
        if peer in self.tcp_connections:
            return self.tcp_connections[peer]

        try:
            # Use getaddrinfo for protocol-agnostic connection
            addr_info = socket.getaddrinfo(peer, self.port, socket.AF_UNSPEC,
                                         socket.SOCK_STREAM, socket.IPPROTO_TCP)

            # Try each address family until one succeeds
            for family, socktype, proto, canonname, sockaddr in addr_info:
                try:
                    sock = socket.socket(family, socktype, proto)
                    sock.settimeout(self.timeout)
                    if family == socket.AF_INET6:
                        # Set IPv6 TTL/hop limit equivalent
                        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_UNICAST_HOPS, self.ttl)
                    else:
                        # Set IPv4 TTL
                        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, self.ttl)

                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    sock.connect(sockaddr)
                    self.tcp_connections[peer] = sock
                    return sock
                except (socket.error, OSError) as e:
                    if 'sock' in locals():
                        sock.close()
                    continue

            # All addresses failed
            return None
        except (socket.error, OSError):
            return None

    def _validate_transmission_effectiveness(self, tx_before: Optional[int], packets_sent: int, attempts: int):
        """Validate that transmission actually increased tx_bytes."""
        if self.network_interface is None:
            logger.debug("No network interface available for tx_bytes validation, using DNS fallback")
            self._trigger_dns_fallback_if_needed()
            return

        tx_after = self._get_tx_bytes()
        if tx_before is None or tx_after is None:
            logger.warning("tx_bytes monitoring unavailable (container environment?), using DNS fallback")
            self._trigger_dns_fallback_if_needed()
            return

        tx_delta = tx_after - tx_before
        expected_bytes = packets_sent * self.packet_size

        # Update EMA
        self.tx_bytes_ema = (self.tx_bytes_alpha * tx_delta +
                           (1 - self.tx_bytes_alpha) * self.tx_bytes_ema)

        # Verify external egress for E2 compliance
        if tx_delta > expected_bytes * 0.6:  # 60% threshold for validation
            if any(self.peers[peer]['is_external'] for peer in self.peers
                  if self.peers[peer]['state'] == PeerState.VALID):
                self.external_egress_verified = True
        else:
            logger.debug(f"Low tx_bytes delta: {tx_delta} bytes vs {expected_bytes} expected")
            self._handle_ineffective_transmission()

    def _trigger_dns_fallback_if_needed(self):
        """Trigger DNS fallback when tx_bytes validation is unavailable."""
        if self.state in [NetworkState.ACTIVE_UDP, NetworkState.ACTIVE_TCP]:
            # Switch to DNS servers for reliable external traffic
            if not any(peer in self.DEFAULT_DNS_SERVERS for peer in self.peers.keys()):
                logger.info("Adding DNS servers to peer list for reliable external traffic validation")
                for dns_server in self.DEFAULT_DNS_SERVERS:
                    if dns_server not in self.peers:
                        self.peers[dns_server] = {
                            'state': PeerState.VALID,
                            'reputation': self.REPUTATION_INITIAL_HIGH,
                            'failures': 0,
                            'successes': 0,
                            'last_attempt': 0.0,
                            'blacklist_until': 0.0,
                            'is_external': True,
                            'hostname': dns_server
                        }

    def _get_tx_bytes(self) -> Optional[int]:
        """Get current tx_bytes count from network interface."""
        if self.network_interface:
            return read_nic_tx_bytes(self.network_interface)
        return None

    def _handle_ineffective_transmission(self):
        """Handle case where transmission appears ineffective."""
        self.validation_failures += 1

        if self.validation_failures >= 3:
            logger.warning("Multiple validation failures, triggering fallback")
            self._trigger_fallback()
            self.validation_failures = 0

    def _trigger_fallback(self):
        """Trigger fallback due to validation failures."""
        if self.state == NetworkState.ACTIVE_UDP:
            self._handle_protocol_failure()
        elif self.state == NetworkState.ACTIVE_TCP:
            self._rotate_to_next_peer()

    def _record_peer_success(self, peer: str):
        """Record successful transmission to peer."""
        if peer in self.peers:
            peer_info = self.peers[peer]
            peer_info['successes'] += 1
            peer_info['reputation'] = min(self.REPUTATION_MAX, peer_info['reputation'] + self.REPUTATION_SUCCESS_INCREMENT)
            peer_info['last_attempt'] = time.time()

    def _record_peer_failure(self, peer: str, error: str):
        """Record failed transmission to peer."""
        if peer in self.peers:
            peer_info = self.peers[peer]
            peer_info['failures'] += 1
            peer_info['reputation'] = max(self.REPUTATION_MIN, peer_info['reputation'] - self.REPUTATION_FAILURE_PENALTY)
            peer_info['last_attempt'] = time.time()

            # Blacklist peer temporarily if reputation is very low
            if peer_info['reputation'] < self.REPUTATION_BLACKLIST_THRESHOLD:
                peer_info['blacklist_until'] = time.time() + self.BLACKLIST_DURATION_SEC
                peer_info['state'] = PeerState.INVALID
                logger.debug(f"Peer {peer} temporarily blacklisted due to low reputation")

    def _update_health_metrics(self, packets_sent: int, attempts: int):
        """Update health scoring metrics."""
        # Update send success rate
        if attempts > 0:
            success_rate = packets_sent / attempts
            self.recent_send_attempts.append(success_rate)

            # Keep only recent attempts
            if len(self.recent_send_attempts) > 100:
                self.recent_send_attempts = self.recent_send_attempts[-100:]

            self.send_success_rate = sum(self.recent_send_attempts) / len(self.recent_send_attempts)

        # Update peer availability
        valid_peers = sum(1 for info in self.peers.values() if info['state'] == PeerState.VALID)
        total_peers = len(self.peers)
        self.peer_availability = valid_peers / total_peers if total_peers > 0 else 0.0

        # Calculate overall health score
        self._calculate_health_score()

    def _calculate_health_score(self):
        """Calculate overall network health score (0-100)."""
        # Base score from current state
        state_scores = {
            NetworkState.ACTIVE_UDP: self.HEALTH_SCORE_ACTIVE_UDP,
            NetworkState.ACTIVE_TCP: self.HEALTH_SCORE_ACTIVE_TCP,
            NetworkState.DEGRADED_LOCAL: self.HEALTH_SCORE_DEGRADED_LOCAL,
            NetworkState.VALIDATING: self.HEALTH_SCORE_VALIDATING,
            NetworkState.INITIALIZING: self.HEALTH_SCORE_INITIALIZING,
            NetworkState.ERROR: self.HEALTH_SCORE_ERROR_OFF,
            NetworkState.OFF: self.HEALTH_SCORE_ERROR_OFF
        }

        state_score = state_scores.get(self.state, self.HEALTH_SCORE_ERROR_OFF)

        # Component scores (weighted according to documented constants)
        send_success_score = self.send_success_rate * self.HEALTH_WEIGHT_SEND_SUCCESS
        tx_bytes_score = min(self.HEALTH_WEIGHT_TX_BYTES,
                           (self.tx_bytes_ema / (self.bucket.rate_mbps * 125000)) * self.HEALTH_WEIGHT_TX_BYTES)
        peer_availability_score = self.peer_availability * self.HEALTH_WEIGHT_PEER_AVAILABILITY
        external_verification_score = self.HEALTH_WEIGHT_EXTERNAL_VERIFICATION if self.external_egress_verified else 0

        # Calculate weighted score
        component_score = (send_success_score + tx_bytes_score +
                          peer_availability_score + external_verification_score)

        # Final score is minimum of state score and component score
        self.health_score = int(min(state_score, component_score))

    def get_health_status(self) -> Dict[str, Any]:
        """Get comprehensive health status for monitoring."""
        return {
            'state': self.state.value,
            'health_score': self.health_score,
            'tx_bytes_ema_bps': int(self.tx_bytes_ema),
            'target_bps': int(self.bucket.rate_mbps * 125000),
            'external_egress_verified': self.external_egress_verified,
            'send_success_rate': round(self.send_success_rate, 3),
            'peer_availability': round(self.peer_availability, 3),
            'validation_failures': self.validation_failures,
            'time_in_state': round(time.monotonic() - self.state_start_time, 1),
            'valid_peers': [addr for addr, info in self.peers.items()
                          if info['state'] == PeerState.VALID],
            'peer_reputation': {addr: round(info['reputation'], 1)
                              for addr, info in self.peers.items()},
            'state_history': self.state_transitions[-5:]  # Last 5 transitions
        }

    def update_rate(self, new_rate_mbps: float):
        """Update target transmission rate."""
        self.bucket.update_rate(new_rate_mbps)

    def stop(self):
        """Stop network generation and cleanup resources."""
        self._transition_state(NetworkState.OFF, "stop() called")

        # Close UDP socket
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None

        # Close all TCP connections
        for peer, conn in list(self.tcp_connections.items()):
            try:
                conn.close()
            except Exception:
                pass
        self.tcp_connections.clear()

        # Reset state
        self.peers.clear()
        self.resolved_targets.clear()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with guaranteed cleanup."""
        self.stop()
        return False


# ---------------------------
# Network client with native generator
# ---------------------------
def net_client_thread(stop_evt: threading.Event, paused_fn, rate_mbit_val: Value):
    """
    Native network traffic generation thread.

    Native Python network traffic generator using token bucket
    rate limiting and socket-based packet generation.

    Args:
        stop_evt: Threading event to signal thread shutdown
        paused_fn: Function returning True if operations should pause
        rate_mbit_val: Shared Value containing target network rate in Mbit/s
    """
    if NET_MODE != "client":
        return

    # Initialize network generator
    generator = None
    last_rate = 0.0

    try:
        while not stop_evt.is_set():
            if paused_fn():
                if generator:
                    generator.stop()
                    generator = None
                time.sleep(2.0)
                continue

            # Get current rate and clamp to bounds
            current_rate = float(rate_mbit_val.value)
            current_rate = max(NET_MIN_RATE, min(NET_MAX_RATE, current_rate))

            # Create or update generator if rate changed
            if generator is None or abs(current_rate - last_rate) > 0.1:
                if generator:
                    generator.stop()

                generator = NetworkGenerator(
                    rate_mbps=current_rate,
                    protocol=NET_PROTOCOL,
                    ttl=NET_TTL,
                    packet_size=NET_PACKET_SIZE,
                    port=NET_PORT
                )

                # Start generator with configured peers or RFC 2544 defaults
                generator.start(NET_PEERS if NET_PEERS else [])
                last_rate = current_rate
                logger.debug(f"Network generator started: {current_rate:.1f} Mbps, {NET_PROTOCOL.upper()}")

            elif generator:
                # Just update rate if generator exists
                generator.update_rate(current_rate)
                last_rate = current_rate

            # Send traffic burst
            if generator:
                burst_duration = max(1, NET_BURST_SEC)
                try:
                    packets_sent = generator.send_burst(burst_duration)
                    if packets_sent > 0:
                        logger.debug(f"Sent {packets_sent} packets in {burst_duration}s burst")
                except Exception as e:
                    logger.debug(f"Network burst error: {e}")

            # Idle window (low CPU usage)
            idle_end = time.time() + NET_IDLE_SEC
            while time.time() < idle_end and not stop_evt.is_set():
                if paused_fn():
                    break
                time.sleep(0.5)

    except Exception as e:
        logger.error(f"Network client thread error: {e}")
    finally:
        # Clean up generator
        if generator:
            generator.stop()
            logger.debug("Network generator stopped")

# ---------------------------
# Health check server
# ---------------------------
class HealthHandler(BaseHTTPRequestHandler):
    """HTTP request handler for health check endpoints"""
    
    def __init__(self, *args, controller_state=None, controller_state_lock=None, metrics_storage=None, **kwargs):
        self.controller_state = controller_state
        self.controller_state_lock = controller_state_lock
        self.metrics_storage = metrics_storage
        super().__init__(*args, **kwargs)
    
    def _sanitize_error(self, error_msg: str) -> str:
        """Sanitize error messages to prevent information disclosure"""
        # Remove potentially sensitive information like file paths, internal details
        if "Permission denied" in error_msg or "permission" in error_msg.lower():
            return "Access denied"
        elif "No such file" in error_msg or "not found" in error_msg.lower():
            return "Resource not found"
        elif "Connection refused" in error_msg or "connection" in error_msg.lower():
            return "Service unavailable"
        elif "database" in error_msg.lower() or "sqlite" in error_msg.lower():
            return "Storage service temporarily unavailable"
        else:
            return "Internal service error"
    
    def log_message(self, format, *args):
        """Override to suppress HTTP access logs for reduced noise."""
        # Suppress HTTP server logs to keep output clean
        pass
    
    def do_GET(self):
        """Handle HTTP GET requests for health check endpoints."""
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        
        if path == "/health":
            self._handle_health()
        elif path == "/metrics":
            self._handle_metrics()
        else:
            self._send_error(404, "Not Found")
    
    def do_POST(self):
        """Handle HTTP POST - not allowed for health endpoints."""
        self._send_method_not_allowed()
    
    def do_PUT(self):
        """Handle HTTP PUT - not allowed for health endpoints."""
        self._send_method_not_allowed()
    
    def do_DELETE(self):
        """Handle HTTP DELETE - not allowed for health endpoints."""
        self._send_method_not_allowed()
    
    def do_PATCH(self):
        """Handle HTTP PATCH - not allowed for health endpoints."""
        self._send_method_not_allowed()
    
    def do_HEAD(self):
        """Handle HTTP HEAD - not allowed for health endpoints."""
        self._send_method_not_allowed()
    
    def do_OPTIONS(self):
        """Handle HTTP OPTIONS - not allowed for health endpoints."""
        self._send_method_not_allowed()
    
    def _send_method_not_allowed(self):
        """Send 405 Method Not Allowed response"""
        error_data = {
            "error": "Method not allowed",
            "message": "Only GET requests are supported",
            "allowed_methods": ["GET"],
            "status_code": 405,
            "timestamp": time.time()
        }
        response_body = json.dumps(error_data, indent=2)
        
        self.send_response(405)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(response_body)))
        self.send_header('Allow', 'GET')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.end_headers()
        
        self.wfile.write(response_body.encode('utf-8'))
    
    def _handle_health(self):
        """Handle /health endpoint requests"""
        try:
            # Get basic system info
            with self.controller_state_lock:
                uptime = time.time() - self.controller_state.get('start_time', time.time())
            
            # Check if persistent metrics storage is working
            storage_ok = self.metrics_storage is not None and self.metrics_storage.db_path is not None
            persistence_ok = storage_ok and CPUP95Controller.PERSISTENT_STORAGE_PATH in self.metrics_storage.db_path
            
            # Determine overall health status - direct access to avoid copy
            is_healthy = True
            status_checks = []
            
            # Check if system is in safety stop due to excessive load
            with self.controller_state_lock:
                paused_state = self.controller_state.get('paused', 0.0)
            if paused_state == 1.0:
                is_healthy = False
                status_checks.append("system_paused_safety_stop")
            
            # Check if persistent metrics storage is functional
            if not storage_ok:
                is_healthy = False
                status_checks.append("metrics_storage_failed")
            elif not persistence_ok:
                is_healthy = False
                status_checks.append("persistence_not_available")
                # Note: Persistence failure marks unhealthy as 7-day P95 calculations require persistent storage

            # Check for storage degradation
            elif self.metrics_storage and self.metrics_storage.is_storage_degraded():
                is_healthy = False
                status_checks.append("storage_degraded")

            # Check for extreme resource usage that might indicate issues
            with self.controller_state_lock:
                cpu_avg = self.controller_state.get('cpu_avg')
                mem_avg = self.controller_state.get('mem_avg')
            if cpu_avg and cpu_avg > CPU_STOP_PCT:
                status_checks.append("cpu_critical")
            if mem_avg and mem_avg > MEM_STOP_PCT:
                status_checks.append("memory_critical")
            
            health_data = {
                "status": "healthy" if is_healthy else "unhealthy",
                "uptime_seconds": round(uptime, 1),
                "timestamp": time.time(),
                "checks": status_checks if status_checks else ["all_systems_operational"],
                "metrics_storage": "available" if storage_ok else "failed",
                "persistence_storage": "available" if persistence_ok else "not_mounted",
                "database_path": self.metrics_storage.db_path if self.metrics_storage else None,
                "load_generation": "paused" if paused_state == 1.0 else "active"
            }

            # Add storage status details if available
            if self.metrics_storage:
                storage_status = self.metrics_storage.get_storage_status()
                health_data["storage_status"] = storage_status
            
            status_code = 200 if is_healthy else 503
            self._send_json_response(status_code, health_data)
            
        except Exception as e:
            sanitized_error = self._sanitize_error(str(e))
            self._send_error(500, f"Health check failed: {sanitized_error}")
    
    def _handle_metrics(self):
        """Handle /metrics endpoint requests"""
        try:
            # Direct access to controller state to avoid copy overhead
            cs = self.controller_state
            
            # Get current metrics
            metrics_data = {
                "timestamp": time.time(),
                "current": {
                    "cpu_percent": cs.get('cpu_pct'),
                    "cpu_avg": cs.get('cpu_avg'),
                    "memory_percent": cs.get('mem_pct'),
                    "memory_avg": cs.get('mem_avg'),
                    "network_percent": cs.get('net_pct'),
                    "network_avg": cs.get('net_avg'),
                    "load_average": cs.get('load_avg'),
                    "duty_cycle": cs.get('duty', 0.0),
                    "network_rate_mbit": cs.get('net_rate', 0.0),
                    "paused": cs.get('paused', 0.0) == 1.0
                },
                "targets": {
                    "cpu_p95_setpoint": CPU_P95_SETPOINT,
                    "memory_target": cs.get('mem_target', MEM_TARGET_PCT),
                    "network_target": cs.get('net_target', NET_TARGET_PCT)
                },
                "configuration": {
                    "cpu_stop_threshold": CPU_STOP_PCT,
                    "memory_stop_threshold": MEM_STOP_PCT,
                    "network_stop_threshold": NET_STOP_PCT,
                    "load_threshold": LOAD_THRESHOLD if LOAD_CHECK_ENABLED else None,
                    "worker_count": N_WORKERS,
                    "control_period": CONTROL_PERIOD,
                    "averaging_window": AVG_WINDOW_SEC
                }
            }
            
            # Add 7-day percentiles if metrics storage is available
            if self.metrics_storage and self.metrics_storage.db_path:
                try:
                    percentiles = {
                        "cpu_p95": self.metrics_storage.get_percentile('cpu'),
                        "memory_p95": self.metrics_storage.get_percentile('mem'),
                        "network_p95": self.metrics_storage.get_percentile('net'),
                        "load_p95": self.metrics_storage.get_percentile('load'),
                        "sample_count_7d": self.metrics_storage.get_sample_count()
                    }
                    metrics_data["percentiles_7d"] = percentiles
                except Exception as e:
                    metrics_data["percentiles_7d"] = {"error": self._sanitize_error(str(e))}
            
            self._send_json_response(200, metrics_data)
            
        except Exception as e:
            sanitized_error = self._sanitize_error(str(e))
            self._send_error(500, f"Metrics retrieval failed: {sanitized_error}")
    
    def _send_json_response(self, status_code, data):
        """Send a JSON response with appropriate headers"""
        response_body = json.dumps(data, indent=2)
        
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(response_body)))
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.end_headers()
        
        self.wfile.write(response_body.encode('utf-8'))
    
    def _send_error(self, status_code, message):
        """Send an error response"""
        error_data = {
            "error": message,
            "status_code": status_code,
            "timestamp": time.time()
        }
        self._send_json_response(status_code, error_data)

def health_server_thread(stop_evt: threading.Event, controller_state: dict, controller_state_lock: threading.Lock, metrics_storage):
    """Run HTTP health check server in a separate thread"""
    if not HEALTH_ENABLED:
        return
    
    def handler_factory(*args, **kwargs):
        """Factory function to create HealthHandler with pre-bound context.

        Returns:
            HealthHandler: Configured handler instance with controller state and metrics
        """
        return HealthHandler(*args, controller_state=controller_state,
                           controller_state_lock=controller_state_lock,
                           metrics_storage=metrics_storage, **kwargs)
    
    try:
        server = HTTPServer((HEALTH_HOST, HEALTH_PORT), handler_factory)
        server.timeout = 1.0  # Short timeout for responsive shutdown
        
        logger.info(f"HTTP server starting on {HEALTH_HOST}:{HEALTH_PORT}")
        
        while not stop_evt.is_set():
            server.handle_request()
            
    except OSError as e:
        logger.error(f"Failed to start HTTP server on port {HEALTH_PORT}: {e}")
    except Exception as e:
        logger.error(f"HTTP server error: {e}")
    finally:
        if 'server' in locals():
            server.server_close()
        logger.info("HTTP server stopped")

# ---------------------------
# Main control loop
# ---------------------------
class EMA4:
    """Container for 4-channel EMA (CPU, memory, network, load)."""

    def __init__(self, period, step):
        """Initialize 4-channel EMA container.

        Args:
            period: Time period for smoothing
            step: Update interval
        """
        self.cpu = EMA(period, step)
        self.mem = EMA(period, step)
        self.net = EMA(period, step)
        self.load = EMA(period, step)

def validate_oracle_configuration():
    """Validate configuration against Oracle Free Tier reclamation rules."""
    if not IS_ORACLE:
        return  # Skip validation for non-Oracle environments
    
    warnings = []
    
    # Check if all targets are below Oracle's 20% threshold
    targets_below_20 = []
    if CPU_P95_TARGET_MIN < 20.0:
        targets_below_20.append(f"CPU_P95_TARGET_MIN={CPU_P95_TARGET_MIN}%")
    if MEM_TARGET_PCT < 20.0 and "A1.Flex" in DETECTED_SHAPE:
        targets_below_20.append(f"MEM_TARGET_PCT={MEM_TARGET_PCT}%")
    if NET_TARGET_PCT < 20.0:
        targets_below_20.append(f"NET_TARGET_PCT={NET_TARGET_PCT}%")
    
    # For A1.Flex, all three metrics matter
    if "A1.Flex" in DETECTED_SHAPE:
        if len(targets_below_20) == 3:
            warnings.append(f"⚠️  CRITICAL: ALL targets are below 20% on A1.Flex shape! Oracle will reclaim this VM.")
            warnings.append(f"   Problematic targets: {', '.join(targets_below_20)}")
            warnings.append(f"   Fix: Set at least one target above 20% to prevent reclamation.")
        elif len(targets_below_20) == 2:
            warnings.append(f"⚠️  WARNING: Two targets below 20% on A1.Flex - risky configuration!")
            warnings.append(f"   Targets below 20%: {', '.join(targets_below_20)}")
    else:
        # For E2 shapes, only CPU and NET matter (memory rule doesn't apply)
        cpu_below = CPU_P95_TARGET_MIN < 20.0
        net_below = NET_TARGET_PCT < 20.0
        if cpu_below and net_below:
            warnings.append(f"⚠️  CRITICAL: Both CPU and NET targets below 20% on E2 shape! Oracle will reclaim this VM.")
            warnings.append(f"   Fix: Set either CPU_P95_TARGET_MIN or NET_TARGET_PCT above 20%.")
    
    # Print all warnings
    for warning in warnings:
        logger.warning(warning)
    
    if warnings and any("CRITICAL" in w for w in warnings):
        logger.warning("⚠️  Configuration may result in VM reclamation! Review targets before proceeding.")

class NetworkFallbackState:
    """
    Manages network fallback state and timing for Oracle VM protection.

    Implements smart fallback logic:
    - E2 shapes: Activate when CPU (p95) AND network both at risk
    - A1 shapes: Activate when CPU (p95), network, AND memory all at risk
    """
    def __init__(self):
        self.active = False
        self.last_change = 0.0
        self.activation_count = 0
        self.last_activation = 0.0
        self.last_deactivation = 0.0

    def should_activate(self, is_e2: bool, cpu_p95: Optional[float], net_avg: Optional[float], mem_avg: Optional[float]) -> bool:
        """
        Determine if network fallback should activate based on Oracle reclamation rules.

        Args:
            is_e2 (bool): True if E2 shape, False if A1
            cpu_p95 (float|None): CPU 95th percentile over 7 days
            net_avg (float|None): Current network utilization average
            mem_avg (float|None): Current memory utilization average

        Returns:
            bool: True if fallback should be active
        """
        if NET_ACTIVATION == 'off':
            return False
        elif NET_ACTIVATION == 'always':
            return True
        elif NET_ACTIVATION != 'adaptive':
            return False  # Invalid mode

        now = time.time()

        # Check minimum on/off time requirements
        if self.active and (now - self.last_activation) < NET_FALLBACK_MIN_ON_SEC:
            return True  # Must stay on for minimum time
        if not self.active and (now - self.last_deactivation) < NET_FALLBACK_MIN_OFF_SEC:
            return False  # Must stay off for minimum time

        # Check debounce period
        if (now - self.last_change) < NET_FALLBACK_DEBOUNCE_SEC:
            return self.active  # No state change during debounce

        # Determine if metrics are at risk based on Oracle rules
        if is_e2:
            # E2 shapes: Only CPU (95th percentile) and network matter for Oracle reclamation
            cpu_at_risk = cpu_p95 is not None and cpu_p95 < NET_FALLBACK_RISK_THRESHOLD_PCT
            net_at_risk = net_avg is not None and net_avg < NET_FALLBACK_START_PCT
            should_activate = cpu_at_risk and net_at_risk
        else:
            # A1 shapes: CPU (95th percentile), network, AND memory all matter for Oracle reclamation
            cpu_at_risk = cpu_p95 is not None and cpu_p95 < NET_FALLBACK_RISK_THRESHOLD_PCT
            net_at_risk = net_avg is not None and net_avg < NET_FALLBACK_START_PCT
            mem_at_risk = mem_avg is not None and mem_avg < NET_FALLBACK_RISK_THRESHOLD_PCT
            should_activate = cpu_at_risk and net_at_risk and mem_at_risk

        # Check stop condition (hysteresis)
        if self.active and net_avg is not None and net_avg > NET_FALLBACK_STOP_PCT:
            should_activate = False

        # Update state if changed
        if should_activate != self.active:
            self.active = should_activate
            self.last_change = now
            if should_activate:
                self.activation_count += 1
                self.last_activation = now
            else:
                self.last_deactivation = now

        return self.active

    def get_ramped_target(self, base_target: float, fallback_target: float) -> float:
        """
        Calculate ramped network target during fallback activation.

        Implements smooth rate transitions over NET_FALLBACK_RAMP_SEC seconds
        from base_target to fallback_target when fallback activates.

        Args:
            base_target (float): Original network target percentage
            fallback_target (float): Fallback network target percentage

        Returns:
            float: Ramped target percentage
        """
        if not self.active:
            return base_target

        # Calculate time since activation
        now = time.time()
        time_since_activation = now - self.last_activation

        # If ramping period is complete or NET_FALLBACK_RAMP_SEC is None/0, return full fallback target
        if NET_FALLBACK_RAMP_SEC is None or NET_FALLBACK_RAMP_SEC <= 0 or time_since_activation >= NET_FALLBACK_RAMP_SEC:
            return fallback_target

        # Calculate ramp progress (0.0 = start, 1.0 = end)
        ramp_progress = time_since_activation / NET_FALLBACK_RAMP_SEC

        # Linear interpolation from base_target to fallback_target
        ramped_target = base_target + (fallback_target - base_target) * ramp_progress

        return ramped_target

    def get_debug_info(self) -> Dict[str, Any]:
        """Get debug information about fallback state."""
        now = time.time()

        # Calculate ramp progress if fallback is active
        ramp_progress_pct = None
        if self.active and self.last_activation > 0 and NET_FALLBACK_RAMP_SEC is not None and NET_FALLBACK_RAMP_SEC > 0:
            time_since_activation = now - self.last_activation
            ramp_progress = min(1.0, time_since_activation / NET_FALLBACK_RAMP_SEC)
            ramp_progress_pct = ramp_progress * 100

        return {
            'active': self.active,
            'activation_count': self.activation_count,
            'seconds_since_change': now - self.last_change,
            'in_debounce': (now - self.last_change) < NET_FALLBACK_DEBOUNCE_SEC if NET_FALLBACK_DEBOUNCE_SEC is not None else False,
            'last_activation_ago': now - self.last_activation if self.last_activation > 0 else None,
            'last_deactivation_ago': now - self.last_deactivation if self.last_deactivation > 0 else None,
            'ramp_progress_pct': ramp_progress_pct,
            'ramp_complete': ramp_progress_pct is not None and ramp_progress_pct >= 100.0
        }

def main():
    """
    Main entry point for LoadShaper - Oracle Cloud VM protection service.

    Prevents Oracle Free Tier VM reclamation by maintaining CPU P95 above 20%
    while respecting system load and resource constraints. Uses adaptive P95-driven
    control with slot-based exceedance budget management.
    """
    # Initialize configuration on first use
    _initialize_config()
    
    load_monitor_status = f"LOAD_THRESHOLD={LOAD_THRESHOLD:.1f}" if LOAD_CHECK_ENABLED else "LOAD_CHECK=disabled"
    health_status = f"HEALTH={HEALTH_HOST}:{HEALTH_PORT}" if HEALTH_ENABLED else "HEALTH=disabled"
    shape_status = f"Oracle={DETECTED_SHAPE}" if IS_ORACLE else f"Generic={DETECTED_SHAPE}"
    template_status = f"template={TEMPLATE_FILE}" if TEMPLATE_FILE else "template=none"
    
    # Validate configuration for Oracle environments
    validate_oracle_configuration()
    
    logger.info(f"[loadshaper v2.2] starting with"
          f" CPU_P95_TARGET={CPU_P95_TARGET_MIN:.1f}-{CPU_P95_TARGET_MAX:.1f}%, MEM_TARGET(excl-cache)={MEM_TARGET_PCT}%, NET_TARGET={NET_TARGET_PCT}% |"
          f" NET_SENSE_MODE={NET_SENSE_MODE}, {load_monitor_status}, {health_status} |"
          f" {shape_status}, {template_status}")

    # CRITICAL FOR ORACLE COMPLIANCE: Run at lowest priority (nice 19)
    # Ensures loadshaper immediately yields CPU to any legitimate workload.
    # This prevents loadshaper from impacting real applications while still
    # maintaining the background activity needed for Oracle compliance.
    try:
        os.nice(19)  # Lowest priority - yield to all legitimate processes
    except Exception:
        pass

    # Shared state for health endpoints with thread safety
    import threading
    controller_state_lock = threading.Lock()
    controller_state = {
        'start_time': time.time(),
        'cpu_pct': 0.0,
        'cpu_avg': None,
        'mem_pct': 0.0,
        'mem_avg': None,
        'net_pct': 0.0,
        'net_avg': None,
        'load_avg': None,
        'duty': 0.0,
        'net_rate': 0.0,
        'paused': 0.0,
        'mem_target': MEM_TARGET_PCT,
        'net_target': NET_TARGET_PCT
    }

    global paused
    duty = Value('d', 0.0)
    paused = Value('d', 0.0)  # 1.0 => paused
    net_rate_mbit = Value('d', max(NET_MIN_RATE, min(NET_MAX_RATE, (NET_MAX_RATE + NET_MIN_RATE)/2.0)))

    workers = [Process(target=cpu_worker, args=(duty, paused), daemon=True) for _ in range(N_WORKERS)]
    for p in workers:
        p.start()

    stop_evt = threading.Event()

    # Setup signal handlers for graceful shutdown
    def handle_shutdown(signum, frame):
        logger.info(f"Received signal {signum}, initiating graceful shutdown")
        stop_evt.set()
        paused.value = 1.0  # Pause all workers immediately
        duty.value = 0.0    # Set CPU duty to 0

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    t_mem = threading.Thread(target=mem_nurse_thread, args=(stop_evt,), daemon=True)
    t_mem.start()

    t_net = threading.Thread(
        target=net_client_thread,
        args=(stop_evt, lambda: paused.value == 1.0, net_rate_mbit),
        daemon=True
    )
    t_net.start()

    # Initialize 7-day metrics storage (needed before health server)
    metrics_storage = MetricsStorage()
    cleanup_counter = 0  # Cleanup old data periodically

    # Initialize P95-driven CPU controller
    cpu_p95_controller = CPUP95Controller(metrics_storage)

    # Initialize network fallback state management
    network_fallback_state = NetworkFallbackState()

    # Start health check server
    t_health = threading.Thread(
        target=health_server_thread,
        args=(stop_evt, controller_state, controller_state_lock, metrics_storage),
        daemon=True
    )
    t_health.start()

    # Jitter
    last_jitter = 0.0
    jitter_next = time.time() + JITTER_PERIOD
    mem_target_now = MEM_TARGET_PCT
    net_target_now = NET_TARGET_PCT

    def apply_jitter(base):
        """Apply random jitter to a base value.

        Args:
            base: Base value to apply jitter to

        Returns:
            float: Base value with jitter applied, never below 0.0
        """
        return max(0.0, base * (1.0 + last_jitter))

    def update_jitter():
        """Update jitter values and recalculate memory/network targets.

        Updates the global last_jitter value and applies it to memory and
        network targets to introduce controlled randomness.
        """
        nonlocal last_jitter, mem_target_now, net_target_now
        if JITTER_PCT <= 0:
            last_jitter = 0.0
        else:
            last_jitter = random.uniform(-JITTER_PCT/100.0, JITTER_PCT/100.0)
        mem_target_now = apply_jitter(MEM_TARGET_PCT)
        net_target_now = apply_jitter(NET_TARGET_PCT)

    update_jitter()

    prev_cpu = read_proc_stat()
    ema = EMA4(AVG_WINDOW_SEC, CONTROL_PERIOD)

    # NIC state
    if NET_SENSE_MODE == "host":
        link_mbit = read_host_nic_speed_mbit(NET_IFACE)
        prev_nic = read_host_nic_bytes(NET_IFACE)
    else:  # container
        link_mbit = NET_LINK_MBIT
        prev_nic = read_container_nic_bytes(NET_IFACE_INNER)
    prev_nic_t = time.time()

    try:
        while not stop_evt.is_set():
            # CPU%
            cpu_pct, prev_cpu = cpu_percent_over(CONTROL_PERIOD, prev_cpu)
            cpu_avg = ema.cpu.update(cpu_pct)

            # MEM% (EXCLUDING cache/buffers for Oracle compliance)
            total_b, free_b, mem_used_no_cache_pct, used_bytes_no_cache, mem_used_incl_cache_pct = read_meminfo()
            mem_avg = ema.mem.update(mem_used_no_cache_pct)

            # NIC utilization
            if NET_SENSE_MODE == "host":
                cur_nic = read_host_nic_bytes(NET_IFACE)
            else:
                cur_nic = read_container_nic_bytes(NET_IFACE_INNER)
            now = time.time()
            dt = now - prev_nic_t if prev_nic_t else CONTROL_PERIOD
            nic_util = nic_utilization_pct(prev_nic, cur_nic, dt, link_mbit)
            prev_nic, prev_nic_t = cur_nic, now

            # Only update EMA when NIC metrics are available
            if nic_util is not None:
                net_avg = ema.net.update(nic_util)
            else:
                # Keep previous average when NIC metrics unavailable
                net_avg = ema.net.val if ema.net.val is not None else None

            # Load average (per-core)
            load_1min, load_5min, load_15min, per_core_load = read_loadavg()
            load_avg = ema.load.update(per_core_load)

            # Calculate network fallback status for health endpoints
            is_e2 = is_e2_shape()
            cpu_p95 = cpu_p95_controller.get_cpu_p95() if cpu_p95_controller else None
            fallback_debug = network_fallback_state.get_debug_info()

            # Update controller state for health endpoints (thread-safe)
            with controller_state_lock:
                controller_state.update({
                    'cpu_pct': cpu_pct,
                    'cpu_avg': cpu_avg,
                    'mem_pct': mem_used_no_cache_pct,
                    'mem_avg': mem_avg,
                    'net_pct': nic_util,
                    'net_avg': net_avg,
                    'load_avg': load_avg,
                    'duty': duty.value,
                    'net_rate': net_rate_mbit.value,
                    'paused': paused.value,
                    'cpu_p95_controller': cpu_p95_controller.get_status(),
                    'mem_target': mem_target_now,
                    'net_target': net_target_now,
                    'network_fallback_active': fallback_debug['active'],
                    'network_fallback_count': fallback_debug['activation_count'],
                    'is_e2_shape': is_e2,
                    'cpu_p95_7d': cpu_p95
                })

            # Store metrics sample for 7-day analysis
            metrics_storage.store_sample(cpu_pct, mem_used_no_cache_pct, nic_util, per_core_load)
            
            # Cleanup old data every ~1000 iterations (roughly every 1.4 hours at 5sec intervals)
            cleanup_counter += 1
            if cleanup_counter >= 1000:
                deleted = metrics_storage.cleanup_old()
                if deleted > 0:
                    logger.info(f"Cleaned up {deleted} old samples")
                cleanup_counter = 0

            # Update jitter
            if time.time() >= jitter_next:
                update_jitter()
                jitter_next = time.time() + JITTER_PERIOD

            # Safety stops (including load contention check)
            load_contention = (LOAD_CHECK_ENABLED and 
                               load_avg is not None and 
                               load_avg > LOAD_THRESHOLD)
            if ((cpu_avg is not None and cpu_avg > CPU_STOP_PCT) or
                (mem_avg is not None and mem_avg > MEM_STOP_PCT) or
                (net_avg is not None and net_avg > NET_STOP_PCT) or
                load_contention):
                if paused.value != 1.0:
                    reason = []
                    if cpu_avg is not None and cpu_avg > CPU_STOP_PCT:
                        reason.append(f"cpu_avg={cpu_avg:.1f}%")
                    if mem_avg is not None and mem_avg > MEM_STOP_PCT:
                        reason.append(f"mem_avg={mem_avg:.1f}%")
                    if net_avg is not None and net_avg > NET_STOP_PCT:
                        reason.append(f"net_avg={net_avg:.1f}%")
                    if load_contention:
                        reason.append(f"load_avg={load_avg:.2f}")
                    logger.warning(f"SAFETY STOP: {' '.join(reason)}")
                paused.value = 1.0
                duty.value = 0.0
                set_mem_target_bytes(0)
                net_rate_mbit.value = NET_MIN_RATE
            else:
                # Individual subsystem control - each operates independently unless load contention
                global_load_ok = (not LOAD_CHECK_ENABLED) or (load_avg is None) or (load_avg < LOAD_RESUME_THRESHOLD)

                # Memory control - independent of CPU/P95
                mem_can_run = global_load_ok and (MEM_TARGET_PCT <= 0 or (mem_avg is None) or (mem_avg < max(0.0, MEM_TARGET_PCT - HYSTERESIS_PCT)))

                # Network control - independent of CPU/P95
                net_can_run = global_load_ok and (NET_TARGET_PCT <= 0 or (net_avg is None) or (net_avg < max(0.0, NET_TARGET_PCT - HYSTERESIS_PCT)))

                # Resume if any subsystem was paused and now can run (CPU always delegates to controller)
                if (global_load_ok or mem_can_run or net_can_run) and paused.value != 0.0:
                    logger.info("RESUME (decoupled subsystem control)")
                    paused.value = 0.0

            # Individual subsystem control - CPU always delegates to P95 controller
            if paused.value == 0.0:
                # CPU P95-driven control - always runs (controller handles all decisions)
                # Always advance slot engine to maintain accurate history
                cpu_p95 = cpu_p95_controller.get_cpu_p95()
                cpu_p95_controller.update_state(cpu_p95)
                is_high_slot, target_intensity = cpu_p95_controller.should_run_high_slot(load_avg)

                # Apply global load safety override if needed
                if not global_load_ok:
                    target_intensity = CPU_P95_BASELINE_INTENSITY
                    # Mark the current slot as low for accurate exceedance tracking
                    cpu_p95_controller.mark_current_slot_low()

                # Convert target intensity to duty cycle
                target_duty = target_intensity / 100.0
                duty.value = min(MAX_DUTY, max(0.0, target_duty))

                # Memory control (only if memory can run)
                if mem_can_run and MEM_TARGET_PCT > 0:
                    desired_used_b = int(total_b * (mem_target_now / 100.0))
                    need_delta_b = desired_used_b - used_bytes_no_cache
                    # Keep some real free memory
                    min_free_b = MEM_MIN_FREE_MB * 1024 * 1024
                    if need_delta_b > 0 and (free_b - need_delta_b) < min_free_b:
                        need_delta_b = max(0, int(free_b - min_free_b))
                    with mem_lock:
                        our_current = len(mem_block)
                    target_alloc = max(0, our_current + need_delta_b)
                    set_mem_target_bytes(target_alloc)
                else:
                    # Memory cannot run - release all
                    set_mem_target_bytes(0)

                # Network fallback decision (Oracle VM protection)
                # Integrate fallback with P95-driven control
                fallback_active = network_fallback_state.should_activate(is_e2, cpu_p95, net_avg, mem_avg)

                # Apply fallback to network target with smooth ramping
                effective_net_target = net_target_now
                if fallback_active and net_target_now < NET_FALLBACK_START_PCT:
                    # Use ramped target for smooth transitions over NET_FALLBACK_RAMP_SEC
                    effective_net_target = network_fallback_state.get_ramped_target(
                        net_target_now, NET_FALLBACK_START_PCT
                    )

                    # Calculate ramp progress for logging
                    now = time.time()
                    time_since_activation = now - network_fallback_state.last_activation
                    ramp_progress = min(1.0, time_since_activation / NET_FALLBACK_RAMP_SEC) * 100

                    logger.info(f"Network fallback ACTIVE (E2={is_e2}, CPU_p95={cpu_p95:.1f}%, "
                              f"Net={net_avg:.1f}%, Mem={mem_avg:.1f}%) -> ramped target={effective_net_target:.1f}% "
                              f"(ramp {ramp_progress:.1f}%)")
                elif network_fallback_state.active and not fallback_active:
                    logger.info(f"Network fallback DEACTIVATED")

                # Network control (only if network can run)
                if net_can_run and NET_TARGET_PCT > 0 and net_avg is not None and NET_MODE == "client":
                    err_net = effective_net_target - net_avg
                    new_rate = float(net_rate_mbit.value) + KP_NET * (err_net)
                    net_rate_mbit.value = max(NET_MIN_RATE, min(NET_MAX_RATE, new_rate))
                else:
                    # Network cannot run - set to minimum
                    net_rate_mbit.value = NET_MIN_RATE

            # Logging
            if cpu_avg is not None and mem_avg is not None and net_avg is not None and load_avg is not None:
                # Get CPU P95 and controller status (only CPU uses P95 per Oracle rules)
                p95_status = cpu_p95_controller.get_status()
                cpu_p95 = p95_status['cpu_p95']

                # Format CPU P95 and controller status for display
                cpu_p95_str = f"p95={cpu_p95:5.1f}%" if cpu_p95 is not None else "p95=n/a"
                controller_status = f"state={p95_status['state']} exceedance={p95_status['exceedance_pct']:.1f}%"
                
                load_status = f"load now={per_core_load:.2f} avg={load_avg:.2f}" if LOAD_CHECK_ENABLED else "load=disabled"
                sample_count = metrics_storage.get_sample_count()
                
                # Memory metric display (Oracle compliance - excludes cache/buffers)
                mem_display = f"mem(excl-cache) now={mem_used_no_cache_pct:5.1f}% avg={mem_avg:5.1f}%"
                # Optionally show both metrics for validation (add DEBUG_MEM_METRICS=true to enable)
                if os.getenv("DEBUG_MEM_METRICS", "false").lower() == "true":
                    mem_display += f" [incl-cache={mem_used_incl_cache_pct:5.1f}%]"
                
                logger.info(f"cpu now={cpu_pct:5.1f}% avg={cpu_avg:5.1f}% {cpu_p95_str} {controller_status} | "
                           f"{mem_display} | "
                           f"nic({NET_SENSE_MODE}:{NET_IFACE if NET_SENSE_MODE=='host' else NET_IFACE_INNER}, link≈{link_mbit:.0f} Mbit) "
                           f"now={'N/A' if nic_util is None else f'{nic_util:5.2f}%'} avg={'N/A' if net_avg is None else f'{net_avg:5.2f}%'} | "
                           f"{load_status} | "
                           f"duty={duty.value:4.2f} paused={int(paused.value)} "
                           f"net_rate≈{net_rate_mbit.value:.1f} Mbit | "
                           f"samples_7d={sample_count}")

    except KeyboardInterrupt:
        pass
    except Exception as e:
        # Log control loop error with sanitized details to prevent information disclosure
        logger.error(f"Control loop fatal error: {type(e).__name__}")
        logger.debug(f"Control loop fatal error details: {e}")
        # Fatal errors in the main loop should cause shutdown
        raise
    finally:
        stop_evt.set()
        duty.value = 0.0
        paused.value = 1.0
        set_mem_target_bytes(0)

        # Gracefully terminate threads and processes
        logger.info("Shutting down threads and processes...")

        # Wait for threads to finish (they check stop_evt)
        if 't_mem' in locals() and t_mem.is_alive():
            t_mem.join(timeout=2.0)
        if 't_net' in locals() and t_net.is_alive():
            t_net.join(timeout=2.0)
        if 't_health' in locals() and t_health.is_alive():
            t_health.join(timeout=2.0)

        # Terminate CPU worker processes
        for p in workers:
            if p.is_alive():
                p.join(timeout=1.0)
                if p.is_alive():
                    p.terminate()
                    p.join(timeout=1.0)

        logger.info("Graceful shutdown complete")

if __name__ == "__main__":
    main()
