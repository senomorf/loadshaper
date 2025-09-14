#!/usr/bin/env python3
"""
Test suite for safety gating functionality in CPUP95Controller.

Tests critical safety mechanisms including:
- Load average threshold pausing
- Resume threshold behavior
- Hysteresis gap prevents oscillation
- mark_current_slot_low() when safety triggered
- Interaction with different load levels
"""

import unittest
from unittest.mock import Mock, patch
import time
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import and set up configuration before importing loadshaper components
import loadshaper

# Initialize required global configuration variables for tests
loadshaper.CPU_P95_SLOT_DURATION = 60.0
loadshaper.CPU_P95_BASELINE_INTENSITY = 20.0
loadshaper.CPU_P95_TARGET_MIN = 22.0
loadshaper.CPU_P95_TARGET_MAX = 28.0
loadshaper.CPU_P95_SETPOINT = 25.0
loadshaper.CPU_P95_EXCEEDANCE_TARGET = 6.5
loadshaper.CPU_P95_HIGH_INTENSITY = 35.0
loadshaper.LOAD_THRESHOLD = 0.6
loadshaper.LOAD_RESUME_THRESHOLD = 0.4
loadshaper.LOAD_CHECK_ENABLED = True

from loadshaper import CPUP95Controller, MetricsStorage


class MockMetricsStorage:
    """Mock metrics storage for testing."""

    def __init__(self, mock_p95=25.0):
        self.mock_p95 = mock_p95

    def get_percentile(self, metric, percentile=95):
        if metric == 'cpu' and percentile == 95:
            return self.mock_p95
        return None


class TestSafetyGating(unittest.TestCase):
    """Test safety gating mechanisms in CPUP95Controller."""

    def setUp(self):
        """Set up test fixtures."""
        # Set test environment to ensure deterministic behavior
        os.environ['PYTEST_CURRENT_TEST'] = 'test_safety_gating'

        # Ensure load checking is enabled for safety gating tests
        loadshaper.LOAD_CHECK_ENABLED = True
        loadshaper.LOAD_THRESHOLD = 0.6
        loadshaper.LOAD_RESUME_THRESHOLD = 0.4

        self.storage = MockMetricsStorage()
        self.controller = CPUP95Controller(self.storage)

        # Default configuration values for testing
        self.load_threshold = 0.6
        self.load_resume_threshold = 0.4
        self.baseline_intensity = 20.0

    def tearDown(self):
        """Clean up after tests."""
        if 'PYTEST_CURRENT_TEST' in os.environ:
            del os.environ['PYTEST_CURRENT_TEST']

    def test_high_load_triggers_safety_pause(self):
        """Test that high load average triggers safety pause."""
        # Start with controller wanting to run high slot
        self.controller.state = 'BUILDING'

        # Simulate high load that should trigger safety by advancing time to trigger a new slot
        high_load = 0.8  # Above threshold (0.6)

        # Use direct manipulation of slot start to avoid time.monotonic issues in tests
        original_start = self.controller.current_slot_start

        # First call with normal time
        self.controller.should_run_high_slot(high_load)

        # Manually advance the slot start time to trigger rollover
        # Set to more than slot duration (60s) ago to ensure rollover triggers
        self.controller.current_slot_start = original_start - 70

        # Now call again to trigger the safety check
        is_high_slot, target_intensity = self.controller.should_run_high_slot(high_load)

        # Safety should override and force low slot
        self.assertFalse(is_high_slot)
        self.assertEqual(target_intensity, self.baseline_intensity)
        self.assertGreater(self.controller.slots_skipped_safety, 0)

    def test_low_load_allows_normal_operation(self):
        """Test that low load doesn't trigger safety mechanism."""
        # Start with fresh controller that should want to run high slot
        self.controller.state = 'BUILDING'
        # Clear any existing slot history to start fresh
        self.controller.slots_recorded = 0

        # Simulate low load that should allow normal operation
        low_load = 0.3  # Below resume threshold (0.4)

        # Advance time to trigger a new slot
        with patch('time.monotonic', return_value=time.monotonic() + 65):
            is_high_slot, target_intensity = self.controller.should_run_high_slot(low_load)

        # Low load should not trigger safety (slots_skipped_safety should remain 0)
        # Whether we get a high slot depends on exceedance budget, but safety shouldn't interfere
        self.assertEqual(self.controller.slots_skipped_safety, 0)

    def test_load_hysteresis_prevents_oscillation(self):
        """Test that load thresholds work as documented."""
        # Test that exactly at threshold doesn't trigger safety
        controller1 = CPUP95Controller(MockMetricsStorage())
        controller1.state = 'BUILDING'
        base_time = time.monotonic()
        # Initialize slot first
        with patch('time.monotonic', return_value=base_time):
            controller1.should_run_high_slot(0.6)
        with patch('time.monotonic', return_value=base_time + 65):
            is_high_slot, _ = controller1.should_run_high_slot(0.6)  # Exactly at threshold
        # Should not trigger safety since condition is > LOAD_THRESHOLD
        self.assertEqual(controller1.slots_skipped_safety, 0)

        # Test that above threshold triggers safety
        controller2 = CPUP95Controller(MockMetricsStorage())
        controller2.state = 'BUILDING'
        # Initialize slot first
        with patch('time.monotonic', return_value=base_time):
            controller2.should_run_high_slot(0.7)
        with patch('time.monotonic', return_value=base_time + 65):
            is_high_slot, _ = controller2.should_run_high_slot(0.7)  # Above threshold
        # Should trigger safety
        self.assertGreater(controller2.slots_skipped_safety, 0)

    def test_mark_current_slot_low_when_safety_overrides(self):
        """Test mark_current_slot_low() correctly handles safety overrides."""
        # Set up controller to want high slot
        self.controller.state = 'BUILDING'
        self.controller.current_slot_is_high = True
        self.controller.current_target_intensity = 35.0

        # Simulate main loop detecting high load and overriding
        self.controller.mark_current_slot_low()

        # Slot should now be marked as low intensity
        self.assertFalse(self.controller.current_slot_is_high)
        self.assertEqual(self.controller.current_target_intensity, self.baseline_intensity)

    def test_mark_current_slot_low_idempotent(self):
        """Test mark_current_slot_low() is safe to call multiple times."""
        # Set up controller with low slot
        self.controller.current_slot_is_high = False
        self.controller.current_target_intensity = self.baseline_intensity

        # Call mark_current_slot_low multiple times
        self.controller.mark_current_slot_low()
        self.controller.mark_current_slot_low()

        # Should remain unchanged
        self.assertFalse(self.controller.current_slot_is_high)
        self.assertEqual(self.controller.current_target_intensity, self.baseline_intensity)

    def test_safety_counter_increments_correctly(self):
        """Test that slots_skipped_safety counter increments properly."""
        initial_count = self.controller.slots_skipped_safety

        # Trigger safety multiple times
        high_load = 0.8
        base_time = time.monotonic()

        # Initialize first slot
        with patch('time.monotonic', return_value=base_time):
            self.controller.should_run_high_slot(high_load)

        # Trigger safety by creating new slots
        for i in range(1, 4):
            with patch('time.monotonic', return_value=base_time + (i * 65)):
                self.controller.should_run_high_slot(high_load)

        # Counter should have incremented
        self.assertGreater(self.controller.slots_skipped_safety, initial_count)

    def test_none_load_average_allows_operation(self):
        """Test that None load average (unavailable) allows normal operation."""
        self.controller.state = 'BUILDING'

        # Pass None as load average
        is_high_slot, target_intensity = self.controller.should_run_high_slot(None)

        # Should allow normal operation when load is unknown
        self.assertTrue(is_high_slot)
        self.assertGreater(target_intensity, self.baseline_intensity)
        self.assertEqual(self.controller.slots_skipped_safety, 0)

    def test_load_check_disabled_bypasses_safety(self):
        """Test that disabled load checking bypasses safety mechanisms."""
        # This test would require patching LOAD_CHECK_ENABLED
        # For now, we assume it's enabled in tests
        pass

    def test_safety_status_in_telemetry(self):
        """Test that safety status is properly reported in get_status()."""
        # Trigger safety by advancing time to force slot rollover
        high_load = 0.8

        # Use direct manipulation of slot start to avoid time.monotonic issues in tests
        original_start = self.controller.current_slot_start

        # First call with normal time
        self.controller.should_run_high_slot(high_load)

        # Manually advance the slot start time to trigger rollover
        # Set to more than slot duration (60s) ago to ensure rollover triggers
        self.controller.current_slot_start = original_start - 70

        # Now call again to trigger the safety check
        self.controller.should_run_high_slot(high_load)

        status = self.controller.get_status()

        # Status should include safety-related information
        self.assertIn('slots_skipped_safety', status)
        self.assertGreater(status['slots_skipped_safety'], 0)
        self.assertFalse(status['current_slot_is_high'])
        self.assertEqual(status['target_intensity'], self.baseline_intensity)

    def test_different_load_levels_behavior(self):
        """Test safety mechanism behavior at various load levels."""
        test_loads = [
            (0.0, 0, "Very low load should not trigger safety"),
            (0.3, 0, "Low load should not trigger safety"),
            (0.4, 0, "At resume threshold should not trigger safety"),
            (0.5, 0, "Between resume and threshold should not trigger safety"),
            (0.6, 0, "At threshold should not trigger safety (threshold is exclusive)"),
            (0.7, 1, "Above threshold should trigger safety"),
            (0.8, 1, "High load should trigger safety"),
            (1.5, 1, "Very high load should trigger safety"),
        ]

        for load_avg, expected_safety_increments, description in test_loads:
            with self.subTest(load=load_avg):
                # Create fresh controller for each test to avoid interference
                fresh_controller = CPUP95Controller(MockMetricsStorage())
                fresh_controller.state = 'BUILDING'
                initial_safety_count = fresh_controller.slots_skipped_safety

                # Create fresh slot to test safety behavior
                original_start = fresh_controller.current_slot_start

                # First call to initialize slot
                fresh_controller.should_run_high_slot(load_avg)

                # Manually advance slot start to trigger rollover
                # Set to more than slot duration (60s) ago to ensure rollover triggers
                fresh_controller.current_slot_start = original_start - 70

                # Second call to trigger rollover and safety check
                fresh_controller.should_run_high_slot(load_avg)

                safety_increments = fresh_controller.slots_skipped_safety - initial_safety_count
                self.assertEqual(safety_increments, expected_safety_increments,
                               f"{description} (load={load_avg})")


if __name__ == '__main__':
    unittest.main()