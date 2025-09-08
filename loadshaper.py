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
MEM_TARGET_PCT    = getenv_float("MEM_TARGET_PCT", 60.0)
CPU_STOP_PCT      = getenv_float("CPU_STOP_PCT", 85.0)
MEM_STOP_PCT      = getenv_float("MEM_STOP_PCT", 90.0)

CONTROL_PERIOD    = getenv_float("CONTROL_PERIOD_SEC", 5.0)
AVG_WINDOW_SEC    = getenv_float("AVG_WINDOW_SEC", 300.0)
HYSTERESIS_PCT    = getenv_float("HYSTERESIS_PCT", 5.0)

JITTER_PCT        = getenv_float("JITTER_PCT", 10.0)
JITTER_PERIOD     = getenv_float("JITTER_PERIOD_SEC", 60.0)

MEM_MIN_FREE_MB   = getenv_int("MEM_MIN_FREE_MB", 512)
MEM_STEP_MB       = getenv_int("MEM_STEP_MB", 64)

NET_MODE          = os.getenv("NET_MODE", "off").strip().lower()
NET_PEERS         = [p.strip() for p in os.getenv("NET_PEERS", "").split(",") if p.strip()]
NET_TARGET_MBIT   = getenv_float("NET_TARGET_MBIT", 50.0)
NET_BURST_SEC     = getenv_int("NET_BURST_SEC", 10)
NET_IDLE_SEC      = getenv_int("NET_IDLE_SEC", 10)
NET_PROTOCOL      = os.getenv("NET_PROTOCOL", "udp").strip().lower()

# PID nice-to-have: use all cores
N_WORKERS = os.cpu_count() or 1

# Controller gains (tuned to be gentle)
KP = 0.30       # proportional gain for CPU duty
MAX_DUTY = 0.95 # never try to pin 100% (gives scheduler breathing room)

# ---------------------------
# Helpers: CPU & memory read
# ---------------------------
def read_proc_stat():
    # Return (total, idle) jiffies from /proc/stat
    with open("/proc/stat", "r") as f:
        line = f.readline()
    if not line.startswith("cpu "):
        raise RuntimeError("Unexpected /proc/stat format")
    parts = line.split()
    # cpu user nice system idle iowait irq softirq steal guest guest_nice
    vals = [float(x) for x in parts[1:11]]
    idle = vals[3] + vals[4]  # idle + iowait
    nonidle = vals[0] + vals[1] + vals[2] + vals[5] + vals[6] + vals[7]
    total = idle + nonidle
    return total, idle

def cpu_percent_over(dt, prev=None):
    # Measure CPU% for whole system over dt seconds
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
    out = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    def kb(key):
        # values like "123456 kB"
        if key not in out:
            return 0
        return int(out[key].split()[0])
    mem_total_kb = kb("MemTotal")
    mem_avail_kb = kb("MemAvailable")
    mem_used_pct = 0.0
    if mem_total_kb > 0:
        mem_used_pct = 100.0 * (mem_total_kb - mem_avail_kb) / mem_total_kb
    return mem_total_kb * 1024, mem_avail_kb * 1024, mem_used_pct

# ---------------------------
# Moving average (EMA)
# ---------------------------
class EMA:
    def __init__(self, period_sec, step_sec, init=None):
        # alpha ~ 2/(N+1) with N = period/step
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
    TICK = 0.1  # seconds
    junk = 1.0
    while True:
        if stop_flag.value == 1.0:
            time.sleep(0.5)
            continue
        d = float(shared_duty.value)
        d = 0.0 if d < 0 else (MAX_DUTY if d > MAX_DUTY else d)
        busy = d * TICK
        start = time.perf_counter()
        # Busy loop
        while (time.perf_counter() - start) < busy:
            # some floating ops to keep the core hot
            junk = junk * 1.0000001 + 1.0
        # Sleep the remainder
        rest = TICK - busy
        if rest > 0:
            time.sleep(rest)

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
        # Step toward target to avoid huge realloc churn
        if target_bytes > cur:
            inc = min(step, target_bytes - cur)
            mem_block.extend(b"\x00" * inc)
        elif target_bytes < cur:
            dec = min(step, cur - target_bytes)
            # shrink by deleting slice
            del mem_block[cur - dec:cur]

def mem_nurse_thread(stop_evt: threading.Event):
    # Touch pages periodically so they're actually resident
    PAGE = 4096
    while not stop_evt.is_set():
        with mem_lock:
            size = len(mem_block)
            if size > 0:
                # Touch roughly every 4KB
                for pos in range(0, size, PAGE):
                    mem_block[pos] = (mem_block[pos] + 1) & 0xFF
        time.sleep(1.0)

# ---------------------------
# Network client (iperf3)
# ---------------------------
def net_client_thread(stop_evt: threading.Event, paused_fn, jitter_fn):
    if NET_MODE != "client" or not NET_PEERS:
        return
    while not stop_evt.is_set():
        if paused_fn():
            time.sleep(2.0)
            continue
        peer = random.choice(NET_PEERS)
        target_mbit = jitter_fn(NET_TARGET_MBIT)
        burst = max(1, NET_BURST_SEC)
        proto_args = ["-u"] if NET_PROTOCOL == "udp" else []
        cmd = ["iperf3"] + proto_args + ["-b", f"{target_mbit}M", "-t", str(burst), "-c", peer]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        # rest
        for _ in range(NET_IDLE_SEC):
            if stop_evt.is_set():
                break
            time.sleep(1.0)

# ---------------------------
# Main control loop
# ---------------------------
def main():
    print("[loadshaper] starting with",
          f"CPU_TARGET={CPU_TARGET_PCT}%, MEM_TARGET={MEM_TARGET_PCT}%, ",
          f"JITTER=±{JITTER_PCT}% (every {JITTER_PERIOD}s), ",
          f"NET_MODE={NET_MODE}, PEERS={NET_PEERS}")

    # Shared duty for workers and a pause flag
    duty = Value('d', 0.0)
    paused = Value('d', 0.0)  # 1.0 => paused (safety stop)
    workers = [Process(target=cpu_worker, args=(duty, paused), daemon=True) for _ in range(N_WORKERS)]
    for p in workers:
        p.start()

    # Memory toucher
    stop_evt = threading.Event()
    t_mem = threading.Thread(target=mem_nurse_thread, args=(stop_evt,), daemon=True)
    t_mem.start()

    # Jitter setup
    last_jitter = 0.0
    jitter_next = time.time() + JITTER_PERIOD
    cpu_target_now = CPU_TARGET_PCT
    mem_target_now = MEM_TARGET_PCT

    def with_jitter(base):
        return max(0.0, base * (1.0 + last_jitter))

    def update_jitter():
        nonlocal last_jitter, cpu_target_now, mem_target_now
        # uniform in [-JITTER_PCT, +JITTER_PCT]
        if JITTER_PCT <= 0:
            last_jitter = 0.0
        else:
            last_jitter = random.uniform(-JITTER_PCT/100.0, JITTER_PCT/100.0)
        cpu_target_now = with_jitter(CPU_TARGET_PCT)
        mem_target_now = with_jitter(MEM_TARGET_PCT)

    update_jitter()

    # Network client thread
    t_net = threading.Thread(
        target=net_client_thread,
        args=(stop_evt, lambda: paused.value == 1.0, with_jitter),
        daemon=True
    )
    t_net.start()

    # EMAs
    cpu_ema = EMA(AVG_WINDOW_SEC, CONTROL_PERIOD)
    mem_ema = EMA(AVG_WINDOW_SEC, CONTROL_PERIOD)

    prev_stat = read_proc_stat()

    try:
        while True:
            # CPU% over the control period
            cpu_pct, prev_stat = cpu_percent_over(CONTROL_PERIOD, prev_stat)
            cpu_avg = cpu_ema.update(cpu_pct)

            # Memory snapshot
            total_b, avail_b, mem_used_pct = read_meminfo()
            mem_avg = mem_ema.update(mem_used_pct)

            # Update jitter periodically
            if time.time() >= jitter_next:
                update_jitter()
                jitter_next = time.time() + JITTER_PERIOD

            # Safety stops based on long averages
            if (cpu_avg is not None and cpu_avg > CPU_STOP_PCT) or (mem_avg is not None and mem_avg > MEM_STOP_PCT):
                if paused.value != 1.0:
                    print(f"[loadshaper] SAFETY STOP: cpu_avg={cpu_avg:.1f}% mem_avg={mem_avg:.1f}%")
                paused.value = 1.0
                duty.value = 0.0
                set_mem_target_bytes(0)
            else:
                # Resume after dropping below (target - hysteresis)
                resume_cpu = (cpu_avg is None) or (cpu_avg < max(0.0, CPU_TARGET_PCT - HYSTERESIS_PCT))
                resume_mem = (mem_avg is None) or (mem_avg < max(0.0, MEM_TARGET_PCT - HYSTERESIS_PCT))
                if resume_cpu and resume_mem:
                    if paused.value != 0.0:
                        print("[loadshaper] RESUME")
                    paused.value = 0.0

            # If running, steer CPU and RAM toward jittered targets
            if paused.value == 0.0:
                # CPU duty adjust (P controller)
                if cpu_avg is not None:
                    err = cpu_target_now - cpu_avg
                    new_duty = duty.value + KP * (err / 100.0)
                    if new_duty < 0.0: new_duty = 0.0
                    if new_duty > MAX_DUTY: new_duty = MAX_DUTY
                    duty.value = new_duty

                # RAM target in bytes: bring system USED toward mem_target_now
                # desired_used = total * mem_target%; current_used = total - avail
                desired_used_b = total_b * (mem_target_now / 100.0)
                current_used_b = total_b - avail_b
                need_delta_b = int(desired_used_b - current_used_b)

                # Always keep some headroom
                min_free_b = MEM_MIN_FREE_MB * 1024 * 1024
                # If we'd violate min free, clamp
                if need_delta_b > 0 and (avail_b - need_delta_b) < min_free_b:
                    need_delta_b = max(0, int(avail_b - min_free_b))

                # Translate this into our own allocation target
                # our_current = len(mem_block). Step toward our_current + need_delta_b
                with mem_lock:
                    our_current = len(mem_block)
                target_alloc = max(0, our_current + need_delta_b)
                set_mem_target_bytes(target_alloc)

            # Logging (lightweight)
            if cpu_avg is not None and mem_avg is not None:
                print(f"[loadshaper] now cpu={cpu_pct:5.1f}% avg={cpu_avg:5.1f}% | "
                      f"mem_used={mem_used_pct:5.1f}% avg={mem_avg:5.1f}% | "
                      f"duty={duty.value:4.2f} paused={int(paused.value)} "
                      f"targets cpu≈{cpu_target_now:.1f}% mem≈{mem_target_now:.1f}%")

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