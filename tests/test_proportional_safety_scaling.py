#!/usr/bin/env python3
"""
Test suite for proportional safety scaling functionality in CPUP95Controller.

Tests the new feature that scales CPU intensity proportionally based on system
load instead of binary baseline/high switching.
"""

import unittest
from unittest.mock import Mock, patch
import time
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set test mode environment variable before importing loadshaper
os.environ['LOADSHAPER_TEST_MODE'] = 'true'

# Import loadshaper components
import loadshaper
from loadshaper import CPUP95Controller, MetricsStorage


class MockMetricsStorage:
    """Mock metrics storage for testing."""

    def __init__(self, mock_p95=25.0):
        self.mock_p95 = mock_p95

    def get_percentile(self, metric, percentile=95):
        if metric == 'cpu' and percentile == 95:
            return self.mock_p95
        return None


@patch.dict(os.environ, {'LOADSHAPER_TEST_MODE': 'true'})
class TestProportionalSafetyScaling(unittest.TestCase):
    """Test proportional safety scaling functionality."""

    def setUp(self):
        """Set up test fixtures."""
        # Set test environment to ensure deterministic behavior
        os.environ['PYTEST_CURRENT_TEST'] = 'test_proportional_safety_scaling'

        # Use patch to isolate global configuration and prevent test pollution
        self.patcher = patch.multiple(
            'loadshaper',
            CPU_P95_SLOT_DURATION=60.0,
            CPU_P95_BASELINE_INTENSITY=20.0,
            CPU_P95_TARGET_MIN=22.0,
            CPU_P95_TARGET_MAX=28.0,
            CPU_P95_SETPOINT=25.0,
            CPU_P95_EXCEEDANCE_TARGET=6.5,
            CPU_P95_HIGH_INTENSITY=35.0,
            LOAD_THRESHOLD=0.6,
            LOAD_RESUME_THRESHOLD=0.4,
            LOAD_CHECK_ENABLED=True
        )
        self.mock_configs = self.patcher.start()

        self.storage = MockMetricsStorage()
        self.controller = CPUP95Controller(self.storage)

        # Test configuration values
        self.baseline_intensity = 20.0
        self.high_intensity = 35.0
        self.load_threshold = 0.6

    def tearDown(self):
        """Clean up after tests."""
        self.patcher.stop()
        if 'PYTEST_CURRENT_TEST' in os.environ:
            del os.environ['PYTEST_CURRENT_TEST']

    def test_proportional_scaling_enabled_by_default(self):
        """Test that proportional scaling is enabled by default."""
        self.assertTrue(self.controller.SAFETY_PROPORTIONAL_ENABLED)
        self.assertAlmostEqual(self.controller.SAFETY_SCALE_START, 0.5, places=2)
        self.assertAlmostEqual(self.controller.SAFETY_SCALE_FULL, 0.8, places=2)
        self.assertAlmostEqual(self.controller.SAFETY_MIN_INTENSITY_SCALE, 0.7, places=2)

    def test_no_scaling_below_start_threshold(self):
        """Test that no scaling occurs below SAFETY_SCALE_START."""
        # Set controller to BUILDING state for predictable behavior
        self.controller.state = 'BUILDING'

        # Load below scaling start (0.5) - should get normal intensity
        low_load = 0.3  # Below SAFETY_SCALE_START

        # Force new slot with this load level
        with patch('time.monotonic', return_value=time.monotonic() + 65):
            is_high, intensity = self.controller.should_run_high_slot(low_load)

        # Should not trigger safety scaling at this low load
        self.assertEqual(self.controller.slots_skipped_safety, 0)

    def test_full_baseline_above_full_threshold(self):
        """Test that full baseline is applied above SAFETY_SCALE_FULL."""
        # Set controller to want high slot
        self.controller.state = 'BUILDING'

        # Load above full scaling (0.8) - should get full baseline
        very_high_load = 0.9  # Above SAFETY_SCALE_FULL

        with patch('time.monotonic', return_value=time.monotonic() + 65):
            is_high, intensity = self.controller.should_run_high_slot(very_high_load)

        # Should trigger safety and use baseline
        self.assertFalse(is_high)
        self.assertEqual(intensity, self.baseline_intensity)
        self.assertGreater(self.controller.slots_skipped_safety, 0)

    def test_proportional_scaling_in_middle_range(self):
        """Test proportional scaling between start and full thresholds."""
        # Set controller to want high slot
        self.controller.state = 'BUILDING'

        # Load in middle of scaling range
        mid_load = 0.65  # Between SAFETY_SCALE_START (0.5) and SAFETY_SCALE_FULL (0.8)

        with patch('time.monotonic', return_value=time.monotonic() + 65):
            is_high, intensity = self.controller.should_run_high_slot(mid_load)

        # Should trigger safety but use scaled intensity (not full baseline)
        self.assertFalse(is_high)
        self.assertGreater(intensity, self.baseline_intensity)  # Scaled, not baseline
        self.assertLess(intensity, self.high_intensity)  # But less than full high
        self.assertGreater(self.controller.slots_skipped_safety, 0)

    def test_scaling_calculation_accuracy(self):
        """Test the accuracy of proportional scaling calculations."""
        # Test the _calculate_safety_scaled_intensity method directly
        self.controller.state = 'BUILDING'

        # At exactly SAFETY_SCALE_START (0.5) - should get normal intensity
        normal_intensity = self.controller.get_target_intensity()
        scaled_intensity = self.controller._calculate_safety_scaled_intensity(0.5, normal_intensity)
        self.assertEqual(scaled_intensity, normal_intensity)

        # At exactly SAFETY_SCALE_FULL (0.8) - should get baseline
        scaled_intensity = self.controller._calculate_safety_scaled_intensity(0.8, normal_intensity)
        self.assertEqual(scaled_intensity, self.baseline_intensity)

        # At 25% through range (0.575) - should be 25% scaled down
        quarter_load = 0.5 + (0.8 - 0.5) * 0.25  # 0.575
        scaled_intensity = self.controller._calculate_safety_scaled_intensity(quarter_load, normal_intensity)

        # Should be between normal and minimum scaled intensity
        min_scaled = normal_intensity * self.controller.SAFETY_MIN_INTENSITY_SCALE  # 70% of normal
        expected_range_bottom = min_scaled
        expected_range_top = normal_intensity
        self.assertGreater(scaled_intensity, expected_range_bottom)
        self.assertLess(scaled_intensity, expected_range_top)

    def test_scaling_respects_baseline_floor(self):
        """Test that scaling never goes below baseline intensity."""
        self.controller.state = 'BUILDING'

        # Even with very high load, should never go below baseline
        extreme_load = 10.0  # Unrealistic but tests the floor
        scaled_intensity = self.controller._calculate_safety_scaled_intensity(extreme_load, 35.0)
        self.assertEqual(scaled_intensity, self.baseline_intensity)

    def test_disabled_proportional_scaling_fallback(self):
        """Test behavior when proportional scaling is disabled."""
        # Temporarily disable proportional scaling
        original_enabled = self.controller.SAFETY_PROPORTIONAL_ENABLED
        self.controller.SAFETY_PROPORTIONAL_ENABLED = False

        try:
            # Any load above threshold should give baseline
            scaled_intensity = self.controller._calculate_safety_scaled_intensity(0.65, 35.0)
            self.assertEqual(scaled_intensity, self.baseline_intensity)
        finally:
            # Restore original setting
            self.controller.SAFETY_PROPORTIONAL_ENABLED = original_enabled

    def test_different_controller_states_with_scaling(self):
        """Test proportional scaling works with different controller states."""
        # Test with BUILDING state (high normal intensity)
        self.controller.state = 'BUILDING'
        building_normal = self.controller.get_target_intensity()
        building_intensity = self.controller._calculate_safety_scaled_intensity(0.65, building_normal)

        # Test with REDUCING state (lower normal intensity)
        self.controller.state = 'REDUCING'
        reducing_normal = self.controller.get_target_intensity()
        reducing_intensity = self.controller._calculate_safety_scaled_intensity(0.65, reducing_normal)

        # Test with MAINTAINING state (setpoint-based intensity)
        self.controller.state = 'MAINTAINING'
        maintaining_normal = self.controller.get_target_intensity()
        maintaining_intensity = self.controller._calculate_safety_scaled_intensity(0.65, maintaining_normal)

        # All should be above baseline but scaled appropriately
        self.assertGreater(building_intensity, self.baseline_intensity)
        self.assertGreater(reducing_intensity, self.baseline_intensity)
        self.assertGreater(maintaining_intensity, self.baseline_intensity)

        # Building should typically have highest normal intensity, thus highest scaled intensity
        self.assertGreater(building_normal, maintaining_normal)
        self.assertGreater(building_intensity, maintaining_intensity)

    def test_gradual_load_increase_response(self):
        """Test system response to gradually increasing load."""
        self.controller.state = 'BUILDING'

        loads = [0.4, 0.5, 0.6, 0.65, 0.7, 0.75, 0.8, 0.9]
        intensities = []

        for load in loads:
            intensity = self.controller._calculate_safety_scaled_intensity(load, 35.0)
            intensities.append(intensity)

        # Intensities should generally decrease as load increases
        # (though there might be some variation due to state-specific calculations)

        # First few should be higher than later ones
        self.assertGreater(intensities[0], intensities[-1])  # 0.4 load vs 0.9 load
        self.assertGreater(intensities[2], intensities[-2])  # 0.6 load vs 0.8 load


if __name__ == '__main__':
    unittest.main()