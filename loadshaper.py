import os
import time
import random
import threading
import subprocess
from multiprocessing import Process, Value
from math import isfinite

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

# Workers equal to CPU count for smoother shaping
N_WORKERS = os.cpu_count() or 1

# Controller gains (gentle)
KP_CPU = 0.30       # proportional gain for CPU duty
KP_NET = 0.60       # proportional gain for iperf rate (Mbps)
MAX_DUTY = 0.95     # CPU duty cap

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
# CPU workers (busy/sleep)
# ---------------------------
def cpu_worker(shared_duty: Value, stop_flag: Value):
    os.nice(19)  # lowest priority; always yield to real workloads
    TICK = 0.1
    junk = 1.0
    while True:
        if stop_flag.value == 1.0:
            time.sleep(SLEEP_SLICE)
            continue
        d = float(shared_duty.value)
        d = 0.0 if d < 0 else (MAX_DUTY if d > MAX_DUTY else d)
        busy = d * TICK
        start = time.perf_counter()
        while (time.perf_counter() - start) < busy:
            junk = junk * 1.0000001 + 1.0
        rest = TICK - busy
        if rest > 0:
            time.sleep(rest)
        else:
            time.sleep(SLEEP_SLICE)

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
class EMA3:
    def __init__(self, period, step):
        self.cpu = EMA(period, step)
        self.mem = EMA(period, step)
        self.net = EMA(period, step)

def main():
    print("[loadshaper v2.2] starting with",
          f" CPU_TARGET={CPU_TARGET_PCT}%, MEM_TARGET(no-cache)={MEM_TARGET_PCT}%, NET_TARGET={NET_TARGET_PCT}% |",
          f" NET_SENSE_MODE={NET_SENSE_MODE}")

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
    ema = EMA3(AVG_WINDOW_SEC, CONTROL_PERIOD)

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

            # Update jitter
            if time.time() >= jitter_next:
                update_jitter()
                jitter_next = time.time() + JITTER_PERIOD

            # Safety stops
            if ((cpu_avg is not None and cpu_avg > CPU_STOP_PCT) or
                (mem_avg is not None and mem_avg > MEM_STOP_PCT) or
                (net_avg is not None and net_avg > NET_STOP_PCT)):
                if paused.value != 1.0:
                    print(f"[loadshaper] SAFETY STOP: cpu_avg={cpu_avg:.1f}% mem_avg={mem_avg:.1f}% net_avg={net_avg:.1f}%")
                paused.value = 1.0
                duty.value = 0.0
                set_mem_target_bytes(0)
                net_rate_mbit.value = NET_MIN_RATE
            else:
                resume_cpu = (cpu_avg is None) or (cpu_avg < max(0.0, CPU_TARGET_PCT - HYSTERESIS_PCT))
                resume_mem = (mem_avg is None) or (mem_avg < max(0.0, MEM_TARGET_PCT - HYSTERESIS_PCT))
                resume_net = (net_avg is None) or (net_avg < max(0.0, NET_TARGET_PCT - HYSTERESIS_PCT))
                if resume_cpu and resume_mem and resume_net:
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
            if cpu_avg is not None and mem_avg is not None and net_avg is not None:
                print(f"[loadshaper] cpu now={cpu_pct:5.1f}% avg={cpu_avg:5.1f}% | "
                      f"mem(no-cache) now={mem_used_no_cache_pct:5.1f}% avg={mem_avg:5.1f}% | "
                      f"nic({NET_SENSE_MODE}:{NET_IFACE if NET_SENSE_MODE=='host' else NET_IFACE_INNER}, link≈{link_mbit:.0f} Mbit) "
                      f"now={nic_util:5.2f}% avg={net_avg:5.2f}% | "
                      f"duty={duty.value:4.2f} paused={int(paused.value)} "
                      f"targets cpu≈{cpu_target_now:.1f}% mem≈{mem_target_now:.1f}% net≈{net_target_now:.1f}% "
                      f"net_rate≈{net_rate_mbit.value:.1f} Mbit")

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
