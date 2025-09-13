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
    if key.endswith('_PCT') or key.startswith('CPU_P95_'):
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
                 'JITTER_PERIOD_SEC']:
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
        ("NET_FALLBACK_RAMP_SEC", 10)
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
    NET_PEERS         = [p.strip() for p in getenv_with_template("NET_PEERS", "", CONFIG_TEMPLATE).split(",") if p.strip()]
    NET_PORT          = getenv_int_with_template("NET_PORT", 15201, CONFIG_TEMPLATE)
    NET_BURST_SEC     = getenv_int_with_template("NET_BURST_SEC", 10, CONFIG_TEMPLATE)
    NET_IDLE_SEC      = getenv_int_with_template("NET_IDLE_SEC", 10, CONFIG_TEMPLATE)
    NET_PROTOCOL      = getenv_with_template("NET_PROTOCOL", "udp", CONFIG_TEMPLATE).strip().lower()

    # New: how we "sense" NIC bytes
    NET_SENSE_MODE    = getenv_with_template("NET_SENSE_MODE", "container", CONFIG_TEMPLATE).strip().lower()  # container|host
    NET_IFACE         = getenv_with_template("NET_IFACE", "ens3", CONFIG_TEMPLATE).strip()        # for host mode (requires /sys mount)
    NET_IFACE_INNER   = getenv_with_template("NET_IFACE_INNER", "eth0", CONFIG_TEMPLATE).strip()  # for container mode (/proc/net/dev)
    NET_LINK_MBIT     = getenv_float_with_template("NET_LINK_MBIT", 1000.0, CONFIG_TEMPLATE)         # used directly in container mode

    # Controller rate bounds (Mbps)
    NET_MIN_RATE      = getenv_float_with_template("NET_MIN_RATE_MBIT", 1.0, CONFIG_TEMPLATE)
    NET_MAX_RATE      = getenv_float_with_template("NET_MAX_RATE_MBIT", 800.0, CONFIG_TEMPLATE)

    # Native network generator configuration
    NET_TTL           = getenv_int_with_template("NET_TTL", 1, CONFIG_TEMPLATE)
    NET_PACKET_SIZE   = getenv_int_with_template("NET_PACKET_SIZE", 8900, CONFIG_TEMPLATE)

    # Network fallback configuration
    NET_ACTIVATION          = getenv_with_template("NET_ACTIVATION", "adaptive", CONFIG_TEMPLATE).strip().lower()
    NET_FALLBACK_START_PCT  = getenv_float_with_template("NET_FALLBACK_START_PCT", 19.0, CONFIG_TEMPLATE)
    NET_FALLBACK_STOP_PCT   = getenv_float_with_template("NET_FALLBACK_STOP_PCT", 23.0, CONFIG_TEMPLATE)
    NET_FALLBACK_RISK_THRESHOLD_PCT = getenv_float_with_template("NET_FALLBACK_RISK_THRESHOLD_PCT", 22.0, CONFIG_TEMPLATE)
    NET_FALLBACK_DEBOUNCE_SEC = getenv_int_with_template("NET_FALLBACK_DEBOUNCE_SEC", 30, CONFIG_TEMPLATE)
    NET_FALLBACK_MIN_ON_SEC = getenv_int_with_template("NET_FALLBACK_MIN_ON_SEC", 60, CONFIG_TEMPLATE)
    NET_FALLBACK_MIN_OFF_SEC = getenv_int_with_template("NET_FALLBACK_MIN_OFF_SEC", 30, CONFIG_TEMPLATE)
    NET_FALLBACK_RAMP_SEC   = getenv_int_with_template("NET_FALLBACK_RAMP_SEC", 10, CONFIG_TEMPLATE)

    # Validate final configuration values (including environment overrides)
    _validate_final_config()
    _validate_network_fallback_config()
    
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
        """
        self.metrics_storage = metrics_storage
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

        # Try to load persisted ring buffer state to solve cold start problem
        self._load_ring_buffer_state()

        # Initialize first slot (no load average available yet)
        self._start_new_slot(current_load_avg=None)

    def get_cpu_p95(self):
        """Get 7-day CPU P95 from metrics storage with caching"""
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

    def _get_ring_buffer_path(self):
        """Get path for ring buffer persistence file"""
        # Use same directory as metrics database for consistency
        try:
            # Try primary location first
            db_dir = "/var/lib/loadshaper"
            if os.path.exists(db_dir) and os.access(db_dir, os.W_OK):
                return os.path.join(db_dir, "p95_ring_buffer.json")
        except (OSError, PermissionError):
            pass

        # Fall back to temp directory
        return "/tmp/loadshaper_p95_ring_buffer.json"

    def _save_ring_buffer_state(self):
        """Save ring buffer state to disk for persistence across restarts"""
        # Skip persistence in test mode for predictable test behavior
        if os.environ.get('PYTEST_CURRENT_TEST'):
            return

        try:
            ring_buffer_path = self._get_ring_buffer_path()
            state = {
                'slot_history': self.slot_history,
                'slot_history_index': self.slot_history_index,
                'slots_recorded': self.slots_recorded,
                'slot_history_size': self.slot_history_size,
                'timestamp': time.time(),  # Use wall clock time for persistence
                'current_slot_is_high': self.current_slot_is_high
            }

            with open(ring_buffer_path, 'w') as f:
                json.dump(state, f)

            logger.debug(f"Saved P95 ring buffer state to {ring_buffer_path}")

        except (OSError, PermissionError, json.JSONEncodeError) as e:
            logger.debug(f"Failed to save P95 ring buffer state: {e}")
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
                logger.debug("No persisted P95 ring buffer state found")
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
            # CRITICAL FIX: Tie high intensity to setpoint for accurate P95 targeting
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
        import os
        if os.environ.get('PYTEST_CURRENT_TEST'):
            # In test mode - return exact values for predictable tests
            return base_intensity
        else:
            # In production mode - add dithering for better P95 control
            import random
            dither = random.uniform(-self.DITHER_RANGE_PCT, self.DITHER_RANGE_PCT)
            dithered_intensity = base_intensity + dither
            # Ensure we stay within reasonable bounds after dithering
            return max(CPU_P95_BASELINE_INTENSITY, min(100.0, dithered_intensity))

    def get_exceedance_target(self):
        """Get adaptive exceedance target based on state and P95 distance from target"""
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
        now = time.monotonic()

        # Handle multiple slot rollovers if process stalled
        while now >= (self.current_slot_start + CPU_P95_SLOT_DURATION):
            self._end_current_slot()
            self._start_new_slot(current_load_avg)

        return self.current_slot_is_high, self.current_target_intensity

    def _end_current_slot(self):
        """End current slot and record its type in history"""
        # Record slot in ring buffer (24-hour sliding window for fast exceedance calculations)
        # Ring buffer avoids expensive database queries for recent slot history
        self.slot_history[self.slot_history_index] = self.current_slot_is_high
        self.slot_history_index = (self.slot_history_index + 1) % self.slot_history_size
        if self.slots_recorded < self.slot_history_size:
            self.slots_recorded += 1  # Don't exceed buffer size

        # Persist ring buffer state for cold start protection
        self._save_ring_buffer_state()

    def _start_new_slot(self, current_load_avg):
        """Start new slot and determine its type"""
        self.current_slot_start = time.monotonic()
        now = self.current_slot_start

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
            self.current_target_intensity = self._calculate_safety_scaled_intensity(current_load_avg)
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
            self.current_target_intensity = self.get_target_intensity()
            # Use reduced intensity for forced slots to minimize system impact
            if current_load_avg is not None and current_load_avg > LOAD_THRESHOLD:
                self.current_target_intensity = self._calculate_safety_scaled_intensity(current_load_avg)
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
        if self.slots_recorded == 0:
            return 0.0
        high_slots = sum(self.slot_history[:self.slots_recorded])
        return high_slots / self.slots_recorded

    def get_current_exceedance(self):
        """Get current exceedance percentage from slot history"""
        return self._calculate_current_exceedance() * 100.0

    def get_status(self):
        """Get controller status for telemetry"""
        cpu_p95 = self.get_cpu_p95()
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
        if self.current_slot_is_high:
            self.current_slot_is_high = False
            self.current_target_intensity = CPU_P95_BASELINE_INTENSITY

    def _calculate_safety_scaled_intensity(self, current_load_avg):
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

        # Calculate what the normal intensity would be
        normal_intensity = self.get_target_intensity()

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

            # Ensure we never go below baseline
            return max(CPU_P95_BASELINE_INTENSITY, scaled_intensity)

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

def read_meminfo() -> Tuple[int, float, int]:
    """
    Read memory usage from /proc/meminfo using industry standards.

    Requires Linux 3.14+ (MemAvailable field). Uses industry-standard calculation
    that excludes cache/buffers for accurate utilization measurement, aligning with
    AWS CloudWatch, Azure Monitor, and Oracle's VM reclamation criteria.

    Returns:
        tuple: (total_bytes, used_pct, used_bytes)
               - total_bytes: Total system memory in bytes
               - used_pct: Memory usage percentage excluding cache/buffers
               - used_bytes: Memory usage in bytes excluding cache/buffers

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
        used_bytes_excl_cache = (total - mem_available) * 1024
    else:
        # FALLBACK METHOD: Manual calculation for older kernels
        buffers = m.get("Buffers", 0)
        cached = m.get("Cached", 0)
        srecl = m.get("SReclaimable", 0)
        shmem = m.get("Shmem", 0)
        buff_cache = buffers + max(0, cached + srecl - shmem)
        used_no_cache = max(0, total - free - buff_cache)
        used_pct_excl_cache = (100.0 * used_no_cache / total) if total > 0 else 0.0
        used_bytes_excl_cache = used_no_cache * 1024

    # Also calculate including cache/buffers for comparison/debugging
    used_incl_cache = max(0, total - free)
    used_pct_incl_cache = (100.0 * used_incl_cache / total) if total > 0 else 0.0

    # Handle corrupt data - clamp to valid range (master compatibility)
    if mem_available > total:
        used_pct_excl_cache = 0.0
        used_bytes_excl_cache = 0
    else:
        used_pct_excl_cache = max(0.0, min(100.0, used_pct_excl_cache))
        used_bytes_excl_cache = max(0, used_bytes_excl_cache)

    return (total * 1024, free * 1024, used_pct_excl_cache, used_bytes_excl_cache, used_pct_incl_cache)

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
                    with fallback to /tmp/loadshaper_metrics.db if permission denied.

        Creates database schema for 7-day metrics storage with thread-safe access.
        """
        if db_path is None:
            # Try to use /var/lib/loadshaper, fallback to /tmp
            try:
                os.makedirs("/var/lib/loadshaper", exist_ok=True)
                db_path = "/var/lib/loadshaper/metrics.db"
            except (OSError, PermissionError):
                db_path = "/tmp/loadshaper_metrics.db"

        self.db_path = db_path
        self.lock = threading.Lock()
        self._init_db()
    
    def _init_db(self):
        """Initialize database schema and handle connection errors.

        Creates the metrics table if it doesn't exist and handles database
        connectivity issues gracefully.
        """
        with self.lock:
            try:
                conn = sqlite3.connect(self.db_path)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS metrics (
                        timestamp REAL PRIMARY KEY,
                        cpu_pct REAL,
                        mem_pct REAL,
                        net_pct REAL,
                        load_avg REAL
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON metrics(timestamp)")
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error(f"Failed to initialize database: {e}")
                # If explicit path was given and failed, try fallback to /tmp
                if self.db_path != "/tmp/loadshaper_metrics.db":
                    logger.warning("Attempting fallback to /tmp")
                    self.db_path = "/tmp/loadshaper_metrics.db"
                    try:
                        conn = sqlite3.connect(self.db_path)
                        conn.execute("""
                            CREATE TABLE IF NOT EXISTS metrics (
                                timestamp REAL PRIMARY KEY,
                                cpu_pct REAL,
                                mem_pct REAL,
                                net_pct REAL,
                                load_avg REAL
                            )
                        """)
                        conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON metrics(timestamp)")
                        conn.commit()
                        conn.close()
                        logger.info(f"Successfully initialized fallback database at {self.db_path}")
                    except Exception as e2:
                        logger.error(f"Fallback to /tmp also failed: {e2}")
                        self.db_path = None
                else:
                    self.db_path = None
    
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
        if self.db_path is None:
            return False
        
        with self.lock:
            try:
                conn = sqlite3.connect(self.db_path)
                timestamp = time.time()
                conn.execute(
                    "INSERT OR REPLACE INTO metrics (timestamp, cpu_pct, mem_pct, net_pct, load_avg) VALUES (?, ?, ?, ?, ?)",
                    (timestamp, cpu_pct, mem_pct, net_pct, load_avg)
                )
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                logger.error(f"Failed to store sample: {e}")
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
        if self.db_path is None:
            return None
        
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
                conn = sqlite3.connect(self.db_path)
                cursor = conn.execute(
                    f"SELECT {column} FROM metrics WHERE timestamp >= ? AND {column} IS NOT NULL ORDER BY {column}",
                    (cutoff_time,)
                )
                values = [row[0] for row in cursor.fetchall()]
                conn.close()
                
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
        if self.db_path is None:
            return 0

        cutoff_time = time.time() - (days_to_keep * 24 * 3600)
        
        with self.lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.execute("DELETE FROM metrics WHERE timestamp < ?", (cutoff_time,))
                deleted = cursor.rowcount
                conn.commit()
                conn.close()
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
        if self.db_path is None:
            return 0

        cutoff_time = time.time() - (days_back * 24 * 3600)
        
        with self.lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.execute("SELECT COUNT(*) FROM metrics WHERE timestamp >= ?", (cutoff_time,))
                count = cursor.fetchone()[0]
                conn.close()
                return count
            except Exception as e:
                logger.error(f"Failed to get sample count: {e}")
                return 0

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

        if elapsed > 0:
            tokens_to_add = elapsed * self.rate_mbps * 1_000_000
            self.tokens = min(self.capacity_bits, self.tokens + tokens_to_add)
            self.last_update = now


class NetworkGenerator:
    """
    Native Python network traffic generator with token bucket rate limiting.

    Provides UDP and TCP traffic generation with RFC 2544 benchmark addresses
    as safe defaults. Supports jumbo frames (MTU 9000) optimization and
    configurable TTL for network safety.
    """

    # RFC 2544 benchmark addresses - safe for testing
    RFC2544_ADDRESSES = ["198.18.0.1", "198.19.255.254"]

    def __init__(self, rate_mbps: float, protocol: str = "udp", ttl: int = 1,
                 packet_size: int = 8900, port: int = 15201, timeout: float = 0.5):
        """
        Initialize network generator.

        Args:
            rate_mbps: Target rate in megabits per second
            protocol: 'udp' or 'tcp'
            ttl: IP Time-to-Live (1 = first hop only for safety)
            packet_size: Packet payload size in bytes
            port: Target port number
            timeout: Connection timeout in seconds for TCP connections
        """
        self.bucket = TokenBucket(rate_mbps)
        self.protocol = protocol.lower()
        self.ttl = max(1, ttl)
        self.packet_size = max(64, min(65507, packet_size))  # UDP max is 65507
        self.port = max(1024, min(65535, port))
        self.timeout = max(0.1, timeout)  # Minimum 0.1s timeout
        self.socket = None
        self.packet_data = None
        self.tcp_connections = {}  # Cache for persistent TCP connections
        self.resolved_targets = {}  # Cache for DNS resolutions
        self.tcp_retry_delays = {}  # Exponential backoff for failed connections
        self.tcp_retry_base_delay = 1.0  # Base delay in seconds
        self.tcp_retry_max_delay = 30.0  # Maximum delay in seconds
        self._prepare_packet_data()

        # Pre-allocate socket buffers for efficiency
        self.send_buffer_size = max(1024 * 1024, self.packet_size * 10)  # 1MB minimum

    def _prepare_packet_data(self):
        """Pre-allocate packet data for zero-copy sending."""
        # Create packet with timestamp and sequence pattern
        timestamp = struct.pack('!d', time.time())
        sequence_pattern = b'LoadShaper-' + (b'x' * (self.packet_size - len(timestamp) - 11))
        self.packet_data = timestamp + sequence_pattern[:self.packet_size - len(timestamp)]

    def update_rate(self, new_rate_mbps: float):
        """Update target transmission rate."""
        self.bucket.update_rate(new_rate_mbps)

    def _resolve_targets(self, target_addresses: list):
        """
        Resolve target addresses to IP addresses with IPv6 support and caching.

        Args:
            target_addresses: List of hostnames or IP addresses to resolve
        """
        resolved = {}
        for target in target_addresses:
            if target in self.resolved_targets:
                # Use cached resolution
                resolved[target] = self.resolved_targets[target]
                continue

            try:
                # Try to resolve hostname to IP address(es)
                # This supports both IPv4 and IPv6
                addr_info = socket.getaddrinfo(target, self.port, socket.AF_UNSPEC,
                                              socket.SOCK_DGRAM if self.protocol == "udp" else socket.SOCK_STREAM)

                # Use first available address (prefer IPv4 for compatibility)
                ipv4_addr = None
                ipv6_addr = None

                for family, sock_type, proto, canonname, sockaddr in addr_info:
                    if family == socket.AF_INET and not ipv4_addr:
                        ipv4_addr = (sockaddr[0], family)
                    elif family == socket.AF_INET6 and not ipv6_addr:
                        ipv6_addr = (sockaddr[0], family)

                # Prefer IPv4 for compatibility, fallback to IPv6
                if ipv4_addr:
                    resolved[target] = ipv4_addr
                elif ipv6_addr:
                    resolved[target] = ipv6_addr
                else:
                    logger.warning(f"Could not resolve {target}, skipping")
                    continue

                # Cache the resolution
                self.resolved_targets[target] = resolved[target]

            except socket.gaierror as e:
                logger.warning(f"Failed to resolve {target}: {e}, skipping")
                continue

        self.target_addresses = resolved

    def start(self, target_addresses: list):
        """
        Start network generation session.

        Args:
            target_addresses: List of target IP addresses/hostnames
        """
        if not target_addresses:
            target_addresses = self.RFC2544_ADDRESSES
            logger.info(f"Using RFC 2544 benchmark addresses for network generation: {target_addresses}")

        # Resolve and cache target addresses with IPv6 support
        self._resolve_targets(target_addresses)

        try:
            if self.protocol == "udp":
                self._start_udp()
            elif self.protocol == "tcp":
                self._start_tcp()
            else:
                raise ValueError(f"Unsupported protocol: {self.protocol}")
        except Exception as e:
            logger.error(f"Failed to start network generator: {e}")
            self.stop()

    def _start_udp(self):
        """Initialize UDP socket for traffic generation with IPv4/IPv6 support."""
        # Determine address family from first resolved target
        if not self.target_addresses:
            raise ValueError("No target addresses resolved")

        # Get address family from first target
        first_target = next(iter(self.target_addresses.values()))
        family = first_target[1]  # AF_INET or AF_INET6

        self.socket = socket.socket(family, socket.SOCK_DGRAM)

        # Set TTL/hop limit for safety
        if family == socket.AF_INET:
            self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, self.ttl)
        elif family == socket.AF_INET6:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_UNICAST_HOPS, self.ttl)

        # Optimize send buffer
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, self.send_buffer_size)

        # Non-blocking mode for better control
        self.socket.setblocking(False)

    def _start_tcp(self):
        """Initialize TCP socket settings for traffic generation."""
        # TCP uses persistent connections managed in tcp_connections dict
        # Set socket to None to indicate TCP mode
        self.socket = None
        # Connections will be created on-demand in _get_tcp_connection()

    def _get_tcp_connection(self, target: str):
        """
        Get or create a persistent TCP connection for the target.

        Args:
            target: Target hostname/IP (key in self.target_addresses)

        Returns:
            socket.socket: Connected TCP socket or None if failed
        """
        if target not in self.target_addresses:
            return None

        ip_addr, family = self.target_addresses[target]

        # Check if we have a cached connection
        if target in self.tcp_connections:
            conn = self.tcp_connections[target]
            try:
                # Test if connection is still alive by trying to send 0 bytes
                conn.send(b'')
                return conn
            except (socket.error, OSError):
                # Connection is dead, remove it
                try:
                    conn.close()
                except:
                    pass
                del self.tcp_connections[target]

        # Check exponential backoff for failed connections
        current_time = time.time()
        if target in self.tcp_retry_delays:
            retry_time, delay = self.tcp_retry_delays[target]
            if current_time < retry_time:
                # Still in backoff period
                return None

        # Create new connection
        try:
            tcp_sock = socket.socket(family, socket.SOCK_STREAM)

            # Apply socket options
            if family == socket.AF_INET:
                tcp_sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, self.ttl)
            elif family == socket.AF_INET6:
                tcp_sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_UNICAST_HOPS, self.ttl)

            tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, self.send_buffer_size)
            tcp_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            tcp_sock.settimeout(self.timeout)  # Configurable timeout

            tcp_sock.connect((ip_addr, self.port))

            # Cache the connection and clear any retry delay
            self.tcp_connections[target] = tcp_sock
            if target in self.tcp_retry_delays:
                del self.tcp_retry_delays[target]
            return tcp_sock

        except (socket.error, OSError) as e:
            # Apply exponential backoff for failed connections
            current_delay = self.tcp_retry_base_delay
            if target in self.tcp_retry_delays:
                _, previous_delay = self.tcp_retry_delays[target]
                current_delay = min(previous_delay * 2, self.tcp_retry_max_delay)

            retry_time = time.time() + current_delay
            self.tcp_retry_delays[target] = (retry_time, current_delay)

            logger.debug(f"Failed to connect to {target} ({ip_addr}): {e}. "
                        f"Retrying after {current_delay:.1f}s")
            return None

    def send_burst(self, duration_seconds: float) -> int:
        """
        Send traffic burst for specified duration.

        Args:
            duration_seconds: How long to send traffic

        Returns:
            int: Number of packets sent
        """
        if not self.target_addresses:
            return 0

        # UDP requires socket, TCP creates connections per packet
        if self.protocol == "udp" and not self.socket:
            return 0

        packets_sent = 0
        start_time = time.time()

        while (time.time() - start_time) < duration_seconds:
            # Check if we can send a packet
            if not self.bucket.can_send(self.packet_size):
                wait_time = self.bucket.wait_time(self.packet_size)
                if wait_time > 0:
                    time.sleep(min(wait_time, 0.001))  # Max 1ms sleep
                continue

            # Select random target
            target = random.choice(list(self.target_addresses.keys()))

            try:
                if self.protocol == "udp":
                    packets_sent += self._send_udp_packet(target)
                elif self.protocol == "tcp":
                    packets_sent += self._send_tcp_packet(target)

                # Consume tokens after successful send
                self.bucket.consume(self.packet_size)

            except Exception as e:
                # Log errors but continue generation
                if packets_sent == 0:  # Only log first error to avoid spam
                    logger.debug(f"Network send error to {target}: {e}")
                time.sleep(0.001)  # Brief pause on error

            # Yield CPU every few packets
            if packets_sent % 100 == 0:
                time.sleep(0.0001)

        return packets_sent

    def _send_udp_packet(self, target: str) -> int:
        """Send single UDP packet with IPv4/IPv6 support."""
        try:
            if target not in self.target_addresses:
                return 0
            ip_addr, family = self.target_addresses[target]

            # Refresh packet timestamp
            current_time = struct.pack('!d', time.time())
            packet = current_time + self.packet_data[8:]

            self.socket.sendto(packet, (ip_addr, self.port))
            return 1
        except socket.error:
            # Expected for non-blocking UDP - just continue
            return 0

    def _send_tcp_packet(self, target: str) -> int:
        """Send TCP packet using persistent connection pool."""
        try:
            # Get or create persistent connection
            tcp_sock = self._get_tcp_connection(target)
            if not tcp_sock:
                return 0

            # Send packet data with current timestamp
            current_time = struct.pack('!d', time.time())
            packet = current_time + self.packet_data[8:]
            tcp_sock.send(packet)
            return 1
        except (socket.error, OSError):
            # Connection failed, remove from pool
            if target in self.tcp_connections:
                try:
                    self.tcp_connections[target].close()
                except:
                    pass
                del self.tcp_connections[target]
            return 0

    def stop(self):
        """Stop network generation and cleanup resources."""
        # Close UDP socket
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None

        # Close all TCP connections
        for target, conn in list(self.tcp_connections.items()):
            try:
                conn.close()
            except Exception:
                pass
        self.tcp_connections.clear()
        self.tcp_retry_delays.clear()  # Clear retry delays

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
            
            # Check if metrics storage is working
            storage_ok = self.metrics_storage is not None and self.metrics_storage.db_path is not None
            
            # Determine overall health status - direct access to avoid copy
            is_healthy = True
            status_checks = []
            
            # Check if system is in safety stop due to excessive load
            with self.controller_state_lock:
                paused_state = self.controller_state.get('paused', 0.0)
            if paused_state == 1.0:
                is_healthy = False
                status_checks.append("system_paused_safety_stop")
            
            # Check if metrics storage is functional
            if not storage_ok:
                status_checks.append("metrics_storage_degraded")
                # Note: Don't mark unhealthy for storage issues, as core functionality still works
            
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
                "metrics_storage": "available" if storage_ok else "degraded",
                "load_generation": "paused" if paused_state == 1.0 else "active"
            }
            
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

    def get_debug_info(self) -> Dict[str, Any]:
        """Get debug information about fallback state."""
        now = time.time()
        return {
            'active': self.active,
            'activation_count': self.activation_count,
            'seconds_since_change': now - self.last_change,
            'in_debounce': (now - self.last_change) < NET_FALLBACK_DEBOUNCE_SEC,
            'last_activation_ago': now - self.last_activation if self.last_activation > 0 else None,
            'last_deactivation_ago': now - self.last_deactivation if self.last_deactivation > 0 else None
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
            total_b, mem_used_pct, used_b = read_meminfo()
            mem_avg = ema.mem.update(mem_used_pct)

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
            cpu_p95 = metrics_storage.get_percentile('cpu') if metrics_storage else None
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
                    need_delta_b = desired_used_b - used_no_cache_b
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

                # Apply fallback to network target (override jittered target if needed)
                effective_net_target = net_target_now
                if fallback_active and net_target_now < NET_FALLBACK_START_PCT:
                    effective_net_target = NET_FALLBACK_START_PCT
                    logger.info(f"Network fallback ACTIVE (E2={is_e2}, CPU_p95={cpu_p95:.1f}%, "
                              f"Net={net_avg:.1f}%, Mem={mem_avg:.1f}%) -> target={effective_net_target:.1f}%")
                elif network_fallback_state.active and not fallback_active:
                    logger.info(f"Network fallback DEACTIVATED")

                # Network control (only if network can run)
                if net_can_run and NET_TARGET_PCT > 0 and net_avg is not None and NET_MODE == "client" and NET_PEERS:
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
