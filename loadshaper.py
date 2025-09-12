import os
import time
import random
import threading
import subprocess
import sqlite3
import json
from multiprocessing import Process, Value
from math import isfinite
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs


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

CPU_TARGET_PCT    = getenv_float("CPU_TARGET_PCT", 30.0)
MEM_TARGET_PCT    = getenv_float("MEM_TARGET_PCT", 60.0)  # excludes cache/buffers
NET_TARGET_PCT    = getenv_float("NET_TARGET_PCT", 10.0)  # NIC utilization %

CPU_STOP_PCT      = getenv_float("CPU_STOP_PCT", 85.0)
MEM_STOP_PCT      = getenv_float("MEM_STOP_PCT", 90.0)
NET_STOP_PCT      = getenv_float("NET_STOP_PCT", 60.0)

CONTROL_PERIOD    = getenv_float("CONTROL_PERIOD_SEC", 5.0)
AVG_WINDOW_SEC    = getenv_float("AVG_WINDOW_SEC", 300.0)
HYSTERESIS_PCT    = getenv_float("HYSTERESIS_PCT", 5.0)

LOAD_THRESHOLD    = getenv_float("LOAD_THRESHOLD", 0.6)      # pause when load avg per core > this (conservative for Oracle Free Tier)
LOAD_RESUME_THRESHOLD = getenv_float("LOAD_RESUME_THRESHOLD", 0.4)  # resume when load avg per core < this (hysteresis)
LOAD_CHECK_ENABLED = os.getenv("LOAD_CHECK_ENABLED", "true").strip().lower() == "true"

JITTER_PCT        = getenv_float("JITTER_PCT", 10.0)
JITTER_PERIOD     = getenv_float("JITTER_PERIOD_SEC", 5.0)

MEM_MIN_FREE_MB   = getenv_int("MEM_MIN_FREE_MB", 512)
MEM_STEP_MB       = getenv_int("MEM_STEP_MB", 64)

NET_MODE          = os.getenv("NET_MODE", "client").strip().lower()
NET_PEERS         = [p.strip() for p in os.getenv("NET_PEERS", "").split(",") if p.strip()]
NET_PORT          = getenv_int("NET_PORT", 15201)
NET_BURST_SEC     = getenv_int("NET_BURST_SEC", 10)
NET_IDLE_SEC      = getenv_int("NET_IDLE_SEC", 10)
NET_PROTOCOL      = os.getenv("NET_PROTOCOL", "udp").strip().lower()

# New: how we "sense" NIC bytes
NET_SENSE_MODE    = os.getenv("NET_SENSE_MODE", "container").strip().lower()  # container|host
NET_IFACE         = os.getenv("NET_IFACE", "ens3").strip()        # for host mode (requires /sys mount)
NET_IFACE_INNER   = os.getenv("NET_IFACE_INNER", "eth0").strip()  # for container mode (/proc/net/dev)
NET_LINK_MBIT     = getenv_float("NET_LINK_MBIT", 1000.0)         # used directly in container mode

# Controller rate bounds (Mbps)
NET_MIN_RATE      = getenv_float("NET_MIN_RATE_MBIT", 1.0)
NET_MAX_RATE      = getenv_float("NET_MAX_RATE_MBIT", 800.0)

# Health check server configuration
HEALTH_PORT       = getenv_int("HEALTH_PORT", 8080)
HEALTH_HOST       = os.getenv("HEALTH_HOST", "127.0.0.1").strip()
HEALTH_ENABLED    = os.getenv("HEALTH_ENABLED", "true").strip().lower() == "true"

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
# Health check server
# ---------------------------
class HealthHandler(BaseHTTPRequestHandler):
    """HTTP request handler for health check endpoints"""
    
    def __init__(self, *args, controller_state=None, metrics_storage=None, **kwargs):
        self.controller_state = controller_state
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
        # Suppress HTTP server logs to keep output clean
        pass
    
    def do_GET(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        
        if path == "/health":
            self._handle_health()
        elif path == "/metrics":
            self._handle_metrics()
        else:
            self._send_error(404, "Not Found")
    
    def do_POST(self):
        self._send_method_not_allowed()
    
    def do_PUT(self):
        self._send_method_not_allowed()
    
    def do_DELETE(self):
        self._send_method_not_allowed()
    
    def do_PATCH(self):
        self._send_method_not_allowed()
    
    def do_HEAD(self):
        self._send_method_not_allowed()
    
    def do_OPTIONS(self):
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
            uptime = time.time() - self.controller_state.get('start_time', time.time())
            
            # Check if metrics storage is working
            storage_ok = self.metrics_storage is not None and self.metrics_storage.db_path is not None
            
            # Determine overall health status - direct access to avoid copy
            is_healthy = True
            status_checks = []
            
            # Check if system is in safety stop due to excessive load
            paused_state = self.controller_state.get('paused', 0.0)
            if paused_state == 1.0:
                is_healthy = False
                status_checks.append("system_paused_safety_stop")
            
            # Check if metrics storage is functional
            if not storage_ok:
                status_checks.append("metrics_storage_degraded")
                # Note: Don't mark unhealthy for storage issues, as core functionality still works
            
            # Check for extreme resource usage that might indicate issues
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
                    "cpu_target": cs.get('cpu_target', CPU_TARGET_PCT),
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

def health_server_thread(stop_evt: threading.Event, controller_state: dict, metrics_storage):
    """Run HTTP health check server in a separate thread"""
    if not HEALTH_ENABLED:
        return
    
    def handler_factory(*args, **kwargs):
        return HealthHandler(*args, controller_state=controller_state, 
                           metrics_storage=metrics_storage, **kwargs)
    
    try:
        server = HTTPServer((HEALTH_HOST, HEALTH_PORT), handler_factory)
        server.timeout = 1.0  # Short timeout for responsive shutdown
        
        print(f"[health] HTTP server starting on {HEALTH_HOST}:{HEALTH_PORT}")
        
        while not stop_evt.is_set():
            server.handle_request()
            
    except OSError as e:
        print(f"[health] Failed to start HTTP server on port {HEALTH_PORT}: {e}")
    except Exception as e:
        print(f"[health] HTTP server error: {e}")
    finally:
        if 'server' in locals():
            server.server_close()
        print("[health] HTTP server stopped")

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
    health_status = f"HEALTH={HEALTH_HOST}:{HEALTH_PORT}" if HEALTH_ENABLED else "HEALTH=disabled"
    print("[loadshaper v2.2] starting with",
          f" CPU_TARGET={CPU_TARGET_PCT}%, MEM_TARGET(no-cache)={MEM_TARGET_PCT}%, NET_TARGET={NET_TARGET_PCT}% |",
          f" NET_SENSE_MODE={NET_SENSE_MODE}, {load_monitor_status}, {health_status}")

    try:
        os.nice(19)  # run controller and workers at lowest priority
    except Exception:
        pass

    # Shared state for health endpoints
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
        'cpu_target': CPU_TARGET_PCT,
        'mem_target': MEM_TARGET_PCT,
        'net_target': NET_TARGET_PCT
    }

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

    # Initialize 7-day metrics storage (needed before health server)
    metrics_storage = MetricsStorage()
    cleanup_counter = 0  # Cleanup old data periodically

    # Start health check server
    t_health = threading.Thread(
        target=health_server_thread,
        args=(stop_evt, controller_state, metrics_storage),
        daemon=True
    )
    t_health.start()

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

            # Update controller state for health endpoints
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
                'cpu_target': cpu_target_now,
                'mem_target': mem_target_now,
                'net_target': net_target_now
            })

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
