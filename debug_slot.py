#!/usr/bin/env python3
import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from unittest.mock import patch
import loadshaper

class MockStorage:
    def __init__(self):
        self.p95_value = 15.0  # Low P95 - should want high exceedance

    def get_percentile(self, metric, percentile=95):
        print(f"Storage queried for P95, returning: {self.p95_value}")
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

    # Clear cache and force fresh P95
    controller._p95_cache = None
    controller._p95_cache_time = 0

    print(f"State: {controller.state}")
    print(f"P95: {controller.get_cpu_p95()}")
    print(f"Exceedance target: {controller.get_exceedance_target()}")
    print(f"Slots recorded: {controller.slots_recorded}")
    print(f"Current exceedance: {controller.get_current_exceedance()}")

    # Test the slot decision
    with patch('time.time', return_value=controller.current_slot_start + 70):
        is_high, intensity = controller.should_run_high_slot(None)
        print(f"Should run high slot: {is_high}, intensity: {intensity}")

        if not is_high:
            print("Test failed! Should be high slot but got low slot")

            # Debug the decision logic
            print(f"Target exceedance: {controller.get_exceedance_target()}")
            print(f"Current exceedance: {controller.get_current_exceedance()}")
            print(f"Exceedance < target? {controller.get_current_exceedance() < controller.get_exceedance_target()}")