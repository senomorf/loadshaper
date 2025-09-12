import os
import time
import random
import threading
import subprocess
import sqlite3
from multiprocessing import Process, Value
from math import isfinite

# ---------------------------
# Oracle shape auto-detection
# ---------------------------

# Cache for shape detection results to avoid repeated API calls
_shape_detection_cache = None


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
    global _shape_detection_cache
    
    # Return cached result if available
    if _shape_detection_cache is not None:
        return _shape_detection_cache
    
    try:
        # Step 1: Try to detect Oracle Cloud instance via DMI/cloud metadata
        is_oracle = _detect_oracle_environment()
        
        # Step 2: Get system specifications with error handling
        cpu_count, total_mem_gb = _get_system_specs()
        
        # Step 3: Determine shape based on characteristics
        if is_oracle:
            shape_name, template_file = _classify_oracle_shape(cpu_count, total_mem_gb)
        else:
            shape_name = f"Generic-{cpu_count}CPU-{total_mem_gb:.1f}GB"
            template_file = None
            
        result = (shape_name, template_file, is_oracle)
        _shape_detection_cache = result
        return result
            
    except Exception as e:
        # On any unexpected error, return safe fallback
        print(f"[shape-detection] Unexpected error during detection: {e}")
        fallback = (f"Unknown-Error-{str(e)[:20]}", None, False)
        _shape_detection_cache = fallback
        return fallback


def _detect_oracle_environment():
    """
    Detect if running on Oracle Cloud using multiple indicators.
    
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
    
    # Method 3: Check for Oracle-specific metadata service (if accessible)
    try:
        import socket
        # Try to connect to Oracle's metadata service (169.254.169.254)
        # This is a quick check without making actual HTTP requests
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)  # Very short timeout
        result = sock.connect_ex(('169.254.169.254', 80))
        sock.close()
        if result == 0:  # Connection successful
            return True
    except (socket.error, ImportError):
        # Socket operations may fail in restricted environments
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
        print(f"[shape-detection] Failed to read memory info: {e}")
        total_mem_gb = 0.0
    
    return cpu_count, total_mem_gb


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
    # Oracle Cloud shape classification with tolerances
    # E2 shapes (shared tenancy)
    if cpu_count == 1 and 0.8 <= total_mem_gb <= 1.2:  # ~1GB
        return ("VM.Standard.E2.1.Micro", "e2-1-micro.env")
    elif cpu_count == 2 and 1.8 <= total_mem_gb <= 2.2:  # ~2GB
        return ("VM.Standard.E2.2.Micro", "e2-2-micro.env")
    
    # A1.Flex shapes (dedicated Ampere)
    elif cpu_count == 1 and 5.5 <= total_mem_gb <= 6.5:  # ~6GB (A1.Flex 1 vCPU)
        return ("VM.Standard.A1.Flex", "a1-flex-1.env")
    elif cpu_count == 4 and 23 <= total_mem_gb <= 25:   # ~24GB (A1.Flex 4 vCPU)
        return ("VM.Standard.A1.Flex", "a1-flex-4.env")
    
    # Unknown Oracle shape - use conservative E2.1.Micro defaults
    else:
        return (f"Oracle-Unknown-{cpu_count}CPU-{total_mem_gb:.1f}GB", "e2-1-micro.env")

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
        {'CPU_TARGET_PCT': '30', 'MEM_TARGET_PCT': '60', ...}
        
        >>> load_config_template(None)
        {}
        
    Note:
        Template files should be located in the 'config-templates/' directory
        relative to the loadshaper.py script location.
    """
    if not template_file:
        return {}
    
    config = {}
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
                            config[key] = value
                    except ValueError:
                        # Invalid line format - skip with warning
                        print(f"[config-template] Warning: Invalid format at "
                              f"{template_file}:{line_num}: {line}")
                        continue
                        
    except (IOError, OSError, UnicodeDecodeError) as e:
        # Template file not found, not readable, or encoding issues
        print(f"[config-template] Warning: Could not load template {template_file}: {e}")
    
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
        >>> # ENV: CPU_TARGET_PCT=50, Template: CPU_TARGET_PCT=30
        >>> getenv_with_template('CPU_TARGET_PCT', '25', template)
        '50'  # Environment variable wins
        
        >>> # ENV: not set, Template: CPU_TARGET_PCT=30  
        >>> getenv_with_template('CPU_TARGET_PCT', '25', template)
        '30'  # Template value used
        
        >>> # ENV: not set, Template: not set
        >>> getenv_with_template('CPU_TARGET_PCT', '25', {})
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
        >>> getenv_float_with_template('CPU_TARGET_PCT', 30.0, {'CPU_TARGET_PCT': '25.5'})
        25.5
        
        >>> getenv_float_with_template('INVALID_NUM', 30.0, {'INVALID_NUM': 'not_a_number'})
        30.0  # Falls back to default on parse error
    """
    try:
        value = getenv_with_template(name, default, config_template)
        return float(value)
    except (ValueError, TypeError) as e:
        print(f"[config] Warning: Failed to parse {name}='{value}' as float, "
              f"using default {default}: {e}")
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
        print(f"[config] Warning: Failed to parse {name}='{value}' as int, "
              f"using default {default}: {e}")
        return int(default)

# ---------------------------
# Env / config
# ---------------------------
def getenv_float(name, default):
    try:
        return float(os.getenv(name, default))
    except Exception:
        return float(default)

def getenv_int(name, default):
    try:
        return int(os.getenv(name, default))
    except Exception:
        return int(default)

# Initialize Oracle shape detection and template loading
DETECTED_SHAPE, TEMPLATE_FILE, IS_ORACLE = detect_oracle_shape()
CONFIG_TEMPLATE = load_config_template(TEMPLATE_FILE)

CPU_TARGET_PCT    = getenv_float_with_template("CPU_TARGET_PCT", 30.0, CONFIG_TEMPLATE)
MEM_TARGET_PCT    = getenv_float_with_template("MEM_TARGET_PCT", 60.0, CONFIG_TEMPLATE)  # excludes cache/buffers
NET_TARGET_PCT    = getenv_float_with_template("NET_TARGET_PCT", 10.0, CONFIG_TEMPLATE)  # NIC utilization %

CPU_STOP_PCT      = getenv_float_with_template("CPU_STOP_PCT", 85.0, CONFIG_TEMPLATE)
MEM_STOP_PCT      = getenv_float_with_template("MEM_STOP_PCT", 90.0, CONFIG_TEMPLATE)
NET_STOP_PCT      = getenv_float_with_template("NET_STOP_PCT", 60.0, CONFIG_TEMPLATE)

CONTROL_PERIOD    = getenv_float_with_template("CONTROL_PERIOD_SEC", 5.0, CONFIG_TEMPLATE)
AVG_WINDOW_SEC    = getenv_float_with_template("AVG_WINDOW_SEC", 300.0, CONFIG_TEMPLATE)
HYSTERESIS_PCT    = getenv_float_with_template("HYSTERESIS_PCT", 5.0, CONFIG_TEMPLATE)

LOAD_THRESHOLD    = getenv_float_with_template("LOAD_THRESHOLD", 0.6, CONFIG_TEMPLATE)      # pause when load avg per core > this (conservative for Oracle Free Tier)
LOAD_RESUME_THRESHOLD = getenv_float_with_template("LOAD_RESUME_THRESHOLD", 0.4, CONFIG_TEMPLATE)  # resume when load avg per core < this (hysteresis)
LOAD_CHECK_ENABLED = getenv_with_template("LOAD_CHECK_ENABLED", "true", CONFIG_TEMPLATE).strip().lower() == "true"

JITTER_PCT        = getenv_float_with_template("JITTER_PCT", 10.0, CONFIG_TEMPLATE)
JITTER_PERIOD     = getenv_float_with_template("JITTER_PERIOD_SEC", 5.0, CONFIG_TEMPLATE)

MEM_MIN_FREE_MB   = getenv_int_with_template("MEM_MIN_FREE_MB", 512, CONFIG_TEMPLATE)
MEM_STEP_MB       = getenv_int_with_template("MEM_STEP_MB", 64, CONFIG_TEMPLATE)

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

# Workers equal to CPU count for smoother shaping
N_WORKERS = os.cpu_count() or 1

# Controller gains (gentle)
KP_CPU = 0.30       # proportional gain for CPU duty
KP_NET = 0.60       # proportional gain for iperf rate (Mbps)
MAX_DUTY = 0.95     # CPU duty cap

# Sleep slice for yielding scheduler - critical for system responsiveness
# 5ms chosen as balance between CPU utilization accuracy and responsiveness:
# - Long enough to avoid excessive context switching overhead
# - Short enough to ensure other processes get timely CPU access
SLEEP_SLICE = 0.005

# ---------------------------
# Helpers: CPU & memory read
# ---------------------------
def read_proc_stat():
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

def read_meminfo():
    # Return host-level (since /proc is global) mem usage excluding cache/buffers
    m = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":", 1)
            parts = v.strip().split()
            m[k] = int(parts[0]) if parts else 0  # in kB
    total = m.get("MemTotal", 0)
    free = m.get("MemFree", 0)
    buffers = m.get("Buffers", 0)
    cached = m.get("Cached", 0)
    srecl = m.get("SReclaimable", 0)
    shmem = m.get("Shmem", 0)
    buff_cache = buffers + max(0, cached + srecl - shmem)
    used_no_cache = max(0, total - free - buff_cache)
    used_pct = (100.0 * used_no_cache / total) if total > 0 else 0.0
    return total * 1024, free * 1024, used_pct, used_no_cache * 1024  # bytes

def read_loadavg():
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
    def __init__(self, period_sec, step_sec, init=None):
        n = max(1.0, period_sec / max(0.1, step_sec))
        self.alpha = 2.0 / (n + 1.0)
        self.val = None if init is None else float(init)
    def update(self, x):
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
                print(f"[metrics] Failed to initialize database: {e}")
                # If explicit path was given and failed, try fallback to /tmp
                if self.db_path != "/tmp/loadshaper_metrics.db":
                    print("[metrics] Attempting fallback to /tmp")
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
                        print(f"[metrics] Successfully initialized fallback database at {self.db_path}")
                    except Exception as e2:
                        print(f"[metrics] Fallback to /tmp also failed: {e2}")
                        self.db_path = None
                else:
                    self.db_path = None
    
    def store_sample(self, cpu_pct, mem_pct, net_pct, load_avg):
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
                print(f"[metrics] Failed to store sample: {e}")
                return False
    
    def get_percentile(self, metric_name, percentile=95.0, days_back=7):
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
                print(f"[metrics] Failed to get percentile: {e}")
                return None
    
    def cleanup_old(self, days_to_keep=7):
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
                print(f"[metrics] Failed to cleanup old data: {e}")
                return 0
    
    def get_sample_count(self, days_back=7):
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
                print(f"[metrics] Failed to get sample count: {e}")
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
    global mem_block
    with mem_lock:
        cur = len(mem_block)
        step = MEM_STEP_MB * 1024 * 1024
        if target_bytes < 0:
            target_bytes = 0
        if target_bytes > cur:
            inc = min(step, target_bytes - cur)
            mem_block.extend(b"\x00" * inc)
        elif target_bytes < cur:
            dec = min(step, cur - target_bytes)
            del mem_block[cur - dec:cur]

def mem_nurse_thread(stop_evt: threading.Event):
    PAGE = 4096
    while not stop_evt.is_set():
        with mem_lock:
            size = len(mem_block)
            if size > 0:
                for pos in range(0, size, PAGE):
                    mem_block[pos] = (mem_block[pos] + 1) & 0xFF
        time.sleep(1.0)

# ---------------------------
# NIC sensing helpers
# ---------------------------
def read_host_nic_bytes(iface: str):
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
    try:
        with open(f"/host_sys_class_net/{iface}/speed", "r") as f:
            sp = float(f.read().strip())
        if sp > 0:
            return sp
    except Exception:
        pass
    return NET_LINK_MBIT

def nic_utilization_pct(prev, cur, dt_sec, link_mbit):
    if prev is None or cur is None or dt_sec <= 0 or link_mbit <= 0:
        return 0.0
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
# Network client (iperf3) with rate control
# ---------------------------
def net_client_thread(stop_evt: threading.Event, paused_fn, rate_mbit_val: Value):
    if NET_MODE != "client" or not NET_PEERS:
        return
    proto_args = ["-u"] if NET_PROTOCOL == "udp" else []
    while not stop_evt.is_set():
        if paused_fn():
            time.sleep(2.0)
            continue
        peer = random.choice(NET_PEERS)
        rate = float(rate_mbit_val.value)
        rate = max(NET_MIN_RATE, min(NET_MAX_RATE, rate))
        burst = max(1, NET_BURST_SEC)

        cmd = ["iperf3"] + proto_args + [
            "-b", f"{rate}M", "-t", str(burst), "-p", str(NET_PORT), "-c", peer
        ]
        try:
            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=burst + 5,
            )
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass

        # idle window (low CPU)
        end = time.time() + NET_IDLE_SEC
        while time.time() < end and not stop_evt.is_set():
            time.sleep(0.5)

# ---------------------------
# Main control loop
# ---------------------------
class EMA4:
    def __init__(self, period, step):
        self.cpu = EMA(period, step)
        self.mem = EMA(period, step)
        self.net = EMA(period, step)
        self.load = EMA(period, step)

def main():
    load_monitor_status = f"LOAD_THRESHOLD={LOAD_THRESHOLD:.1f}" if LOAD_CHECK_ENABLED else "LOAD_CHECK=disabled"
    shape_status = f"Oracle={DETECTED_SHAPE}" if IS_ORACLE else f"Generic={DETECTED_SHAPE}"
    template_status = f"template={TEMPLATE_FILE}" if TEMPLATE_FILE else "template=none"
    print("[loadshaper v2.2] starting with",
          f" CPU_TARGET={CPU_TARGET_PCT}%, MEM_TARGET(no-cache)={MEM_TARGET_PCT}%, NET_TARGET={NET_TARGET_PCT}% |",
          f" NET_SENSE_MODE={NET_SENSE_MODE}, {load_monitor_status} |",
          f" {shape_status}, {template_status}")

    try:
        os.nice(19)  # run controller and workers at lowest priority
    except Exception:
        pass

    duty = Value('d', 0.0)
    paused = Value('d', 0.0)  # 1.0 => paused
    net_rate_mbit = Value('d', max(NET_MIN_RATE, min(NET_MAX_RATE, (NET_MAX_RATE + NET_MIN_RATE)/2.0)))

    workers = [Process(target=cpu_worker, args=(duty, paused), daemon=True) for _ in range(N_WORKERS)]
    for p in workers:
        p.start()

    stop_evt = threading.Event()
    t_mem = threading.Thread(target=mem_nurse_thread, args=(stop_evt,), daemon=True)
    t_mem.start()

    t_net = threading.Thread(
        target=net_client_thread,
        args=(stop_evt, lambda: paused.value == 1.0, net_rate_mbit),
        daemon=True
    )
    t_net.start()

    # Jitter
    last_jitter = 0.0
    jitter_next = time.time() + JITTER_PERIOD
    cpu_target_now = CPU_TARGET_PCT
    mem_target_now = MEM_TARGET_PCT
    net_target_now = NET_TARGET_PCT

    def apply_jitter(base):
        return max(0.0, base * (1.0 + last_jitter))

    def update_jitter():
        nonlocal last_jitter, cpu_target_now, mem_target_now, net_target_now
        if JITTER_PCT <= 0:
            last_jitter = 0.0
        else:
            last_jitter = random.uniform(-JITTER_PCT/100.0, JITTER_PCT/100.0)
        cpu_target_now = apply_jitter(CPU_TARGET_PCT)
        mem_target_now = apply_jitter(MEM_TARGET_PCT)
        net_target_now = apply_jitter(NET_TARGET_PCT)

    update_jitter()

    prev_cpu = read_proc_stat()
    ema = EMA4(AVG_WINDOW_SEC, CONTROL_PERIOD)
    
    # Initialize 7-day metrics storage
    metrics_storage = MetricsStorage()
    cleanup_counter = 0  # Cleanup old data periodically

    # NIC state
    if NET_SENSE_MODE == "host":
        link_mbit = read_host_nic_speed_mbit(NET_IFACE)
        prev_nic = read_host_nic_bytes(NET_IFACE)
    else:  # container
        link_mbit = NET_LINK_MBIT
        prev_nic = read_container_nic_bytes(NET_IFACE_INNER)
    prev_nic_t = time.time()

    try:
        while True:
            # CPU%
            cpu_pct, prev_cpu = cpu_percent_over(CONTROL_PERIOD, prev_cpu)
            cpu_avg = ema.cpu.update(cpu_pct)

            # MEM% (EXCLUDING cache/buffers)
            total_b, free_b, mem_used_no_cache_pct, used_no_cache_b = read_meminfo()
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
            net_avg = ema.net.update(nic_util)

            # Load average (per-core)
            load_1min, load_5min, load_15min, per_core_load = read_loadavg()
            load_avg = ema.load.update(per_core_load)

            # Store metrics sample for 7-day analysis
            metrics_storage.store_sample(cpu_pct, mem_used_no_cache_pct, nic_util, per_core_load)
            
            # Cleanup old data every ~1000 iterations (roughly every 1.4 hours at 5sec intervals)
            cleanup_counter += 1
            if cleanup_counter >= 1000:
                deleted = metrics_storage.cleanup_old()
                if deleted > 0:
                    print(f"[metrics] Cleaned up {deleted} old samples")
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
                    print(f"[loadshaper] SAFETY STOP: {' '.join(reason)}")
                paused.value = 1.0
                duty.value = 0.0
                set_mem_target_bytes(0)
                net_rate_mbit.value = NET_MIN_RATE
            else:
                resume_cpu = (cpu_avg is None) or (cpu_avg < max(0.0, CPU_TARGET_PCT - HYSTERESIS_PCT))
                resume_mem = (mem_avg is None) or (mem_avg < max(0.0, MEM_TARGET_PCT - HYSTERESIS_PCT))
                resume_net = (net_avg is None) or (net_avg < max(0.0, NET_TARGET_PCT - HYSTERESIS_PCT))
                resume_load = (not LOAD_CHECK_ENABLED) or (load_avg is None) or (load_avg < LOAD_RESUME_THRESHOLD)
                if resume_cpu and resume_mem and resume_net and resume_load:
                    if paused.value != 0.0:
                        print("[loadshaper] RESUME")
                    paused.value = 0.0

            # If running, steer CPU, MEM, NET toward jittered targets
            if paused.value == 0.0:
                # CPU duty
                if cpu_avg is not None:
                    err = cpu_target_now - cpu_avg
                    new_duty = duty.value + KP_CPU * (err / 100.0)
                    duty.value = min(MAX_DUTY, max(0.0, new_duty))

                # RAM target (no-cache used)
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

                # NET rate control (Mbps)
                if net_avg is not None and NET_MODE == "client" and NET_PEERS:
                    err_net = net_target_now - net_avg
                    new_rate = float(net_rate_mbit.value) + KP_NET * (err_net)
                    net_rate_mbit.value = max(NET_MIN_RATE, min(NET_MAX_RATE, new_rate))

            # Logging
            if cpu_avg is not None and mem_avg is not None and net_avg is not None and load_avg is not None:
                # Get 95th percentile values for 7-day metrics
                cpu_p95 = metrics_storage.get_percentile('cpu')
                mem_p95 = metrics_storage.get_percentile('mem')
                net_p95 = metrics_storage.get_percentile('net')
                load_p95 = metrics_storage.get_percentile('load')
                
                # Format percentile values for display
                cpu_p95_str = f"p95={cpu_p95:5.1f}%" if cpu_p95 is not None else "p95=n/a"
                mem_p95_str = f"p95={mem_p95:5.1f}%" if mem_p95 is not None else "p95=n/a"
                net_p95_str = f"p95={net_p95:5.2f}%" if net_p95 is not None else "p95=n/a"
                load_p95_str = f"p95={load_p95:.2f}" if load_p95 is not None else "p95=n/a"
                
                load_status = f"load now={per_core_load:.2f} avg={load_avg:.2f} {load_p95_str}" if LOAD_CHECK_ENABLED else "load=disabled"
                sample_count = metrics_storage.get_sample_count()
                
                print(f"[loadshaper] cpu now={cpu_pct:5.1f}% avg={cpu_avg:5.1f}% {cpu_p95_str} | "
                      f"mem(no-cache) now={mem_used_no_cache_pct:5.1f}% avg={mem_avg:5.1f}% {mem_p95_str} | "
                      f"nic({NET_SENSE_MODE}:{NET_IFACE if NET_SENSE_MODE=='host' else NET_IFACE_INNER}, link≈{link_mbit:.0f} Mbit) "
                      f"now={nic_util:5.2f}% avg={net_avg:5.2f}% {net_p95_str} | "
                      f"{load_status} | "
                      f"duty={duty.value:4.2f} paused={int(paused.value)} "
                      f"targets cpu≈{cpu_target_now:.1f}% mem≈{mem_target_now:.1f}% net≈{net_target_now:.1f}% "
                      f"net_rate≈{net_rate_mbit.value:.1f} Mbit | "
                      f"samples_7d={sample_count}")

    except KeyboardInterrupt:
        pass
    finally:
        stop_evt.set()
        duty.value = 0.0
        paused.value = 1.0
        set_mem_target_bytes(0)
        print("[loadshaper] exiting...")

if __name__ == "__main__":
    main()
