#!/usr/bin/env python3
import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from unittest.mock import patch
import loadshaper

class MockStorage:
    def __init__(self):
        self.p95_value = 15.0

    def get_percentile(self, metric, percentile=95):
        return self.p95_value

with patch.multiple(loadshaper,
                   CPU_P95_SLOT_DURATION=60.0,
                   CPU_P95_BASELINE_INTENSITY=20.0,
                   CPU_P95_HIGH_INTENSITY=35.0,
                   CPU_P95_TARGET_MIN=22.0,
                   CPU_P95_TARGET_MAX=28.0,
                   CPU_P95_EXCEEDANCE_TARGET=6.5,
                   LOAD_CHECK_ENABLED=True,
                   LOAD_THRESHOLD=0.6):

    storage = MockStorage()
    controller = loadshaper.CPUP95Controller(storage)

    # Set up the test scenario exactly like the test
    controller._p95_cache = None
    controller._p95_cache_time = 0

    print("=== BEFORE RESET ===")
    print(f"P95: {controller.get_cpu_p95()}")
    print(f"State: {controller.state}")
    print(f"Slots recorded: {controller.slots_recorded}")
    print(f"Current exceedance: {controller.get_current_exceedance()}")
    print(f"Exceedance target: {controller.get_exceedance_target()}")
    print(f"Current slot start: {controller.current_slot_start}")
    print(f"Current slot is high: {controller.current_slot_is_high}")

    # Reset like in the test
    controller.slots_recorded = 0
    for i in range(controller.slot_history_size):
        controller.slot_history[i] = False

    print("\n=== AFTER RESET ===")
    print(f"Slots recorded: {controller.slots_recorded}")
    print(f"Current exceedance: {controller.get_current_exceedance()}")
    print(f"Exceedance target: {controller.get_exceedance_target()}")

    # Test the decision
    print(f"\n=== CALLING should_run_high_slot ===")
    start_time = controller.current_slot_start
    print(f"Current time will be: {start_time + 70}")

    with patch('time.time', return_value=start_time + 70):
        print("Calling should_run_high_slot...")
        is_high, intensity = controller.should_run_high_slot(None)

    print(f"Result: is_high={is_high}, intensity={intensity}")
    print(f"Final slots recorded: {controller.slots_recorded}")
    print(f"Final current exceedance: {controller.get_current_exceedance()}")