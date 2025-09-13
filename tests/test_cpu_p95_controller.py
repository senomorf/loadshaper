import unittest
import time
import tempfile
import os
from unittest.mock import Mock, patch, MagicMock
import sys
import logging

# Add the parent directory to sys.path so we can import loadshaper
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import loadshaper
from loadshaper import CPUP95Controller, MetricsStorage


class MockMetricsStorage:
    """Mock metrics storage for testing"""

    def __init__(self):
        self.p95_value = 25.0  # Default reasonable P95 value
        self.call_count = 0

    def get_percentile(self, metric, percentile=95):
        """Mock get_percentile that tracks call count"""
        self.call_count += 1
        return self.p95_value

    def set_p95(self, value):
        """Set the P95 value to return"""
        self.p95_value = value
        # Don't reset call count - tests expect cumulative counting

    def clear_controller_cache(self, controller):
        """Clear P95 cache in controller when changing mock values"""
        controller._p95_cache = None
        controller._p95_cache_time = 0


class TestCPUP95Controller(unittest.TestCase):
    """Test suite for CPUP95Controller class"""

    def setUp(self):
        """Set up test fixtures"""
        self.mock_storage = MockMetricsStorage()

        # Patch all required constants before creating controller
        self.patches = patch.multiple(loadshaper,
                                    CPU_P95_SLOT_DURATION=60.0,
                                    CPU_P95_BASELINE_INTENSITY=20.0,
                                    CPU_P95_HIGH_INTENSITY=35.0,
                                    CPU_P95_TARGET_MIN=22.0,
                                    CPU_P95_TARGET_MAX=28.0,
                                    CPU_P95_EXCEEDANCE_TARGET=6.5,
                                    LOAD_CHECK_ENABLED=True,
                                    LOAD_THRESHOLD=0.6)
        self.patches.start()

        # Create controller after patching
        self.controller = CPUP95Controller(self.mock_storage)

    def tearDown(self):
        """Clean up test fixtures"""
        self.patches.stop()

    def test_initialization(self):
        """Test controller initialization"""
        self.assertEqual(self.controller.state, 'MAINTAINING')
        self.assertIsNotNone(self.controller.current_slot_start)
        self.assertEqual(self.controller.slots_skipped_safety, 0)

        # Ring buffer should be sized for 24h = 1440 slots at 60s each
        expected_size = int((24 * 60 * 60) / 60)  # 1440
        self.assertEqual(self.controller.slot_history_size, expected_size)
        self.assertEqual(len(self.controller.slot_history), expected_size)

        # Caching fields (should be populated after initialization call)
        self.assertEqual(self.controller._p95_cache, 25.0)  # From MockMetricsStorage default
        self.assertGreater(self.controller._p95_cache_time, 0)
        self.assertEqual(self.controller._p95_cache_ttl_sec, 180)

    def test_dynamic_ring_buffer_sizing(self):
        """Test ring buffer size calculation with different slot durations"""
        with patch.multiple(loadshaper, CPU_P95_SLOT_DURATION=30.0):
            controller = CPUP95Controller(self.mock_storage)
            # 24h = 86400s, 30s slots = 2880 slots
            expected_size = int((24 * 60 * 60) / 30)
            self.assertEqual(controller.slot_history_size, expected_size)

        with patch.multiple(loadshaper, CPU_P95_SLOT_DURATION=120.0):
            controller = CPUP95Controller(self.mock_storage)
            # 24h = 86400s, 120s slots = 720 slots
            expected_size = int((24 * 60 * 60) / 120)
            self.assertEqual(controller.slot_history_size, expected_size)


class TestP95Caching(unittest.TestCase):
    """Test P95 caching behavior"""

    def setUp(self):
        self.mock_storage = MockMetricsStorage()
        self.patches = patch.multiple(loadshaper,
                                    CPU_P95_SLOT_DURATION=60.0,
                                    CPU_P95_BASELINE_INTENSITY=20.0,
                                    CPU_P95_HIGH_INTENSITY=35.0,
                                    CPU_P95_TARGET_MIN=22.0,
                                    CPU_P95_TARGET_MAX=28.0,
                                    CPU_P95_EXCEEDANCE_TARGET=6.5)
        self.patches.start()
        self.controller = CPUP95Controller(self.mock_storage)

    def tearDown(self):
        self.patches.stop()
        # Clean up test environment
        if 'PYTEST_CURRENT_TEST' in os.environ:
            del os.environ['PYTEST_CURRENT_TEST']

    def test_cache_hit_within_ttl(self):
        """Test that cache returns same value within TTL"""
        self.mock_storage.set_p95(25.0)

        # First call should query storage
        p95_1 = self.controller.get_cpu_p95()
        self.assertEqual(p95_1, 25.0)
        self.assertEqual(self.mock_storage.call_count, 1)

        # Second call within TTL should use cache
        p95_2 = self.controller.get_cpu_p95()
        self.assertEqual(p95_2, 25.0)
        self.assertEqual(self.mock_storage.call_count, 1)  # No additional call

    def test_cache_miss_after_ttl(self):
        """Test that cache fetches fresh after TTL expires"""
        self.mock_storage.set_p95(25.0)

        # First call
        p95_1 = self.controller.get_cpu_p95()
        self.assertEqual(p95_1, 25.0)
        self.assertEqual(self.mock_storage.call_count, 1)

        # Advance time beyond TTL
        with patch('time.monotonic', return_value=time.monotonic() + 200):
            self.mock_storage.set_p95(30.0)
            p95_2 = self.controller.get_cpu_p95()
            self.assertEqual(p95_2, 30.0)
            self.assertEqual(self.mock_storage.call_count, 2)

    def test_none_result_doesnt_update_cache(self):
        """Test that None results don't update cache"""
        self.mock_storage.set_p95(25.0)

        # First call gets valid value
        p95_1 = self.controller.get_cpu_p95()
        self.assertEqual(p95_1, 25.0)

        # Advance time and return None
        with patch('time.monotonic', return_value=time.monotonic() + 200):
            self.mock_storage.set_p95(None)
            p95_2 = self.controller.get_cpu_p95()
            self.assertIsNone(p95_2)

            # Cache should retain the previous valid value, not be overwritten with None
            self.assertEqual(self.controller._p95_cache, 25.0)

    def test_multiple_calls_within_ttl_single_query(self):
        """Test that multiple calls within TTL only query DB once"""
        self.mock_storage.set_p95(25.0)

        # Multiple calls within TTL
        for _ in range(5):
            p95 = self.controller.get_cpu_p95()
            self.assertEqual(p95, 25.0)

        # Should only have called storage once
        self.assertEqual(self.mock_storage.call_count, 1)


class TestStateMachine(unittest.TestCase):
    """Test state machine behavior"""

    def setUp(self):
        self.mock_storage = MockMetricsStorage()
        self.patches = patch.multiple(loadshaper,
                                    CPU_P95_SLOT_DURATION=60.0,
                                    CPU_P95_BASELINE_INTENSITY=20.0,
                                    CPU_P95_HIGH_INTENSITY=35.0,
                                    CPU_P95_TARGET_MIN=22.0,
                                    CPU_P95_TARGET_MAX=28.0,
                                    CPU_P95_EXCEEDANCE_TARGET=6.5)
        self.patches.start()
        self.controller = CPUP95Controller(self.mock_storage)

    def tearDown(self):
        self.patches.stop()
        # Clean up test environment
        if 'PYTEST_CURRENT_TEST' in os.environ:
            del os.environ['PYTEST_CURRENT_TEST']

    def test_building_state_transition(self):
        """Test transition to BUILDING state"""
        # P95 well below target should trigger BUILDING
        self.controller.update_state(cpu_p95=18.0)  # 22.0 - 2.5 - 1.5 = 18.0 (recent change hysteresis)
        self.assertEqual(self.controller.state, 'BUILDING')

    def test_reducing_state_transition(self):
        """Test transition to REDUCING state"""
        # P95 well above target should trigger REDUCING
        self.controller.update_state(cpu_p95=32.0)  # 28.0 + 2.5 + 1.5 = 32.0 (recent change hysteresis)
        self.assertEqual(self.controller.state, 'REDUCING')

    def test_maintaining_state_transition(self):
        """Test transition to MAINTAINING state"""
        # Start in BUILDING
        self.controller.state = 'BUILDING'

        # P95 in target range but needs buffer zone
        self.controller.update_state(cpu_p95=25.0)  # Within 22-28 and buffer zone
        self.assertEqual(self.controller.state, 'MAINTAINING')

    def test_adaptive_hysteresis_recent_change(self):
        """Test larger hysteresis after recent state change"""
        # Simulate recent state change
        self.controller.last_state_change = time.time() - 100  # 100s ago (< 300s)

        # Should use larger hysteresis (2.5)
        # Need to be below 22.0 - 2.5 = 19.5 to trigger BUILDING
        self.controller.update_state(cpu_p95=20.0)
        self.assertEqual(self.controller.state, 'MAINTAINING')  # Not low enough

        self.controller.update_state(cpu_p95=19.0)
        self.assertEqual(self.controller.state, 'BUILDING')  # Now low enough

    def test_adaptive_hysteresis_stable_period(self):
        """Test smaller hysteresis after stable period"""
        # Simulate old state change
        self.controller.last_state_change = time.monotonic() - 400  # 400s ago (> 300s)

        # Should use smaller hysteresis (1.0)
        # Need to be below 22.0 - 1.0 = 21.0 to trigger BUILDING
        self.controller.update_state(cpu_p95=21.5)
        self.assertEqual(self.controller.state, 'MAINTAINING')  # Not low enough

        self.controller.update_state(cpu_p95=20.5)
        self.assertEqual(self.controller.state, 'BUILDING')  # Now low enough

    def test_state_change_logging(self):
        """Test that state changes are logged and timed"""
        old_time = self.controller.last_state_change

        with patch('loadshaper.logger') as mock_logger:
            self.controller.update_state(cpu_p95=18.0)

            # Should have logged the change
            mock_logger.info.assert_called()
            log_message = mock_logger.info.call_args[0][0]
            self.assertIn('MAINTAINING → BUILDING', log_message)

            # Should have updated timestamp
            self.assertGreater(self.controller.last_state_change, old_time)

    def test_no_state_change_no_logging(self):
        """Test that staying in same state doesn't log"""
        with patch('loadshaper.logger') as mock_logger:
            # P95 within maintaining range
            self.controller.update_state(cpu_p95=25.0)

            # Should not have logged
            mock_logger.info.assert_not_called()

    def test_none_p95_no_update(self):
        """Test that None P95 doesn't update state"""
        original_state = self.controller.state
        self.controller.update_state(cpu_p95=None)
        self.assertEqual(self.controller.state, original_state)


class TestIntensityCalculation(unittest.TestCase):
    """Test target intensity calculation"""

    def setUp(self):
        # Set test environment to ensure deterministic behavior
        os.environ['PYTEST_CURRENT_TEST'] = 'test_intensity_calculation'

        self.mock_storage = MockMetricsStorage()
        self.patches = patch.multiple(loadshaper,
                                    CPU_P95_SLOT_DURATION=60.0,
                                    CPU_P95_BASELINE_INTENSITY=20.0,
                                    CPU_P95_HIGH_INTENSITY=35.0,
                                    CPU_P95_TARGET_MIN=22.0,
                                    CPU_P95_TARGET_MAX=28.0,
                                    CPU_P95_EXCEEDANCE_TARGET=6.5)
        self.patches.start()
        self.controller = CPUP95Controller(self.mock_storage)

    def tearDown(self):
        self.patches.stop()
        # Clean up test environment
        if 'PYTEST_CURRENT_TEST' in os.environ:
            del os.environ['PYTEST_CURRENT_TEST']

    def set_p95_and_clear_cache(self, value):
        """Helper to set P95 value and clear controller cache"""
        self.mock_storage.set_p95(value)
        self.controller._p95_cache = None
        self.controller._p95_cache_time = 0

    def test_building_intensity_far_below(self):
        """Test BUILDING intensity when far below target"""
        self.controller.state = 'BUILDING'
        self.mock_storage.set_p95(15.0)  # 22.0 - 5.0 - 2.0 = 15.0
        # Clear cache to force fresh query
        self.controller._p95_cache = None
        self.controller._p95_cache_time = 0

        intensity = self.controller.get_target_intensity()
        expected = 35.0 + 8.0  # HIGH + 8 for very aggressive catch-up
        self.assertEqual(intensity, expected)

    def test_building_intensity_near_target(self):
        """Test BUILDING intensity when near target"""
        self.controller.state = 'BUILDING'
        self.mock_storage.set_p95(19.0)  # Above 15.0 threshold

        intensity = self.controller.get_target_intensity()
        expected = 35.0 + 5.0  # HIGH + 5 for normal catch-up
        self.assertEqual(intensity, expected)

    def test_reducing_intensity_far_above(self):
        """Test REDUCING intensity when far above target"""
        self.controller.state = 'REDUCING'
        self.set_p95_and_clear_cache(40.0)  # 28.0 + 10.0 + 2.0 = 40.0

        intensity = self.controller.get_target_intensity()
        expected = 35.0 - 5.0  # HIGH - 5 for conservative
        self.assertEqual(intensity, expected)

    def test_reducing_intensity_near_target(self):
        """Test REDUCING intensity when near target"""
        self.controller.state = 'REDUCING'
        self.mock_storage.set_p95(32.0)  # Below 38.0 threshold

        intensity = self.controller.get_target_intensity()
        expected = 35.0 - 2.0  # HIGH - 2 for moderate reduction
        self.assertEqual(intensity, expected)

    def test_maintaining_intensity_below_midpoint(self):
        """Test MAINTAINING intensity below midpoint"""
        self.controller.state = 'MAINTAINING'
        midpoint = (22.0 + 28.0) / 2  # 25.0
        self.set_p95_and_clear_cache(24.0)  # Below midpoint

        intensity = self.controller.get_target_intensity()
        # New algorithm: setpoint + adjustment. P95=24.0, setpoint=25.0, error=-1.0
        # adjustment = -(-1.0)*0.2 = 0.2, result = 25.0 + 0.2 = 25.2
        expected = 25.2  # Setpoint-based intensity with proportional adjustment
        self.assertAlmostEqual(intensity, expected, places=1)

    def test_maintaining_intensity_above_midpoint(self):
        """Test MAINTAINING intensity above midpoint"""
        self.controller.state = 'MAINTAINING'
        self.set_p95_and_clear_cache(26.0)  # Above midpoint (25.0)

        intensity = self.controller.get_target_intensity()
        # New algorithm: setpoint + adjustment. P95=26.0, setpoint=25.0, error=1.0
        # adjustment = -(1.0)*0.2 = -0.2, result = 25.0 - 0.2 = 24.8
        expected = 24.8  # Setpoint-based intensity with proportional adjustment
        self.assertAlmostEqual(intensity, expected, places=1)

    def test_maintaining_intensity_at_midpoint(self):
        """Test MAINTAINING intensity exactly at midpoint"""
        self.controller.state = 'MAINTAINING'
        self.set_p95_and_clear_cache(25.0)  # Exactly at midpoint

        intensity = self.controller.get_target_intensity()
        # New algorithm: setpoint + adjustment. P95=25.0, setpoint=25.0, error=0.0
        # adjustment = 0.0, result = 25.0
        expected = 25.0  # Exactly at setpoint
        self.assertAlmostEqual(intensity, expected, places=1)

    def test_baseline_floor_enforcement(self):
        """Test that intensity never goes below baseline"""
        self.controller.state = 'REDUCING'

        # Simulate scenario where computed intensity would be below baseline
        with patch.multiple(loadshaper,
                           CPU_P95_HIGH_INTENSITY=22.0,  # Very low high intensity
                           CPU_P95_BASELINE_INTENSITY=25.0):  # Higher baseline
            controller = CPUP95Controller(self.mock_storage)
            controller.state = 'REDUCING'
            self.mock_storage.set_p95(40.0)  # Far above target

            intensity = controller.get_target_intensity()
            # Should be floored at baseline (25.0), not 22.0 - 5.0 = 17.0
            self.assertEqual(intensity, 25.0)

    def test_none_p95_handling(self):
        """Test intensity calculation with None P95"""
        self.controller.state = 'MAINTAINING'
        self.mock_storage.set_p95(None)

        intensity = self.controller.get_target_intensity()
        # New algorithm: With None P95, defaults to setpoint
        expected = 25.0  # Default to setpoint when no P95 data
        self.assertAlmostEqual(intensity, expected, places=1)


class TestExceedanceTargets(unittest.TestCase):
    """Test exceedance target calculation"""

    def setUp(self):
        self.mock_storage = MockMetricsStorage()
        self.patches = patch.multiple(loadshaper,
                                    CPU_P95_SLOT_DURATION=60.0,
                                    CPU_P95_BASELINE_INTENSITY=20.0,
                                    CPU_P95_HIGH_INTENSITY=35.0,
                                    CPU_P95_TARGET_MIN=22.0,
                                    CPU_P95_TARGET_MAX=28.0,
                                    CPU_P95_EXCEEDANCE_TARGET=6.5)
        self.patches.start()
        self.controller = CPUP95Controller(self.mock_storage)

    def tearDown(self):
        self.patches.stop()
        # Clean up test environment
        if 'PYTEST_CURRENT_TEST' in os.environ:
            del os.environ['PYTEST_CURRENT_TEST']

    def set_p95_and_clear_cache(self, value):
        """Helper to set P95 value and clear controller cache"""
        self.mock_storage.set_p95(value)
        self.controller._p95_cache = None
        self.controller._p95_cache_time = 0

    def test_building_exceedance_far_below(self):
        """Test BUILDING exceedance target when far below"""
        self.controller.state = 'BUILDING'
        self.set_p95_and_clear_cache(15.0)  # Far below 22.0 - 5.0 = 17.0

        exceedance = self.controller.get_exceedance_target()
        expected = min(12.0, 6.5 + 4.0)  # base + 4, capped at 12
        self.assertEqual(exceedance, expected)

    def test_building_exceedance_near_target(self):
        """Test BUILDING exceedance target when near target"""
        self.controller.state = 'BUILDING'
        self.mock_storage.set_p95(19.0)  # Above far-below threshold

        exceedance = self.controller.get_exceedance_target()
        expected = 6.5 + 1.0  # base + 1 for building
        self.assertEqual(exceedance, expected)

    def test_reducing_exceedance_far_above(self):
        """Test REDUCING exceedance target when far above"""
        self.controller.state = 'REDUCING'
        self.set_p95_and_clear_cache(40.0)  # Far above 28.0 + 10.0 = 38.0

        exceedance = self.controller.get_exceedance_target()
        expected = 1.0  # Very low for fast reduction
        self.assertEqual(exceedance, expected)

    def test_reducing_exceedance_near_target(self):
        """Test REDUCING exceedance target when near target"""
        self.controller.state = 'REDUCING'
        self.mock_storage.set_p95(32.0)  # Below far-above threshold

        exceedance = self.controller.get_exceedance_target()
        expected = 2.5  # Moderate reduction
        self.assertEqual(exceedance, expected)

    def test_maintaining_exceedance_below_midpoint(self):
        """Test MAINTAINING exceedance - should be stable regardless of P95 position"""
        self.controller.state = 'MAINTAINING'
        midpoint = (22.0 + 28.0) / 2  # 25.0
        self.set_p95_and_clear_cache(24.0)  # Below midpoint

        exceedance = self.controller.get_exceedance_target()
        expected = 6.5  # Base target - no adjustment in MAINTAINING state
        self.assertEqual(exceedance, expected)

    def test_maintaining_exceedance_above_midpoint(self):
        """Test MAINTAINING exceedance - should be stable regardless of P95 position"""
        self.controller.state = 'MAINTAINING'
        self.set_p95_and_clear_cache(26.0)  # Above midpoint

        exceedance = self.controller.get_exceedance_target()
        expected = 6.5  # Base target - no adjustment in MAINTAINING state
        self.assertEqual(exceedance, expected)

    def test_maintaining_exceedance_at_midpoint(self):
        """Test MAINTAINING exceedance exactly at midpoint"""
        self.controller.state = 'MAINTAINING'
        self.mock_storage.set_p95(25.0)  # Exactly at midpoint

        exceedance = self.controller.get_exceedance_target()
        expected = 6.5  # Exactly base
        self.assertEqual(exceedance, expected)


class TestSlotEngine(unittest.TestCase):
    """Test slot engine behavior"""

    def setUp(self):
        self.mock_storage = MockMetricsStorage()
        self.patches = patch.multiple(loadshaper,
                                    CPU_P95_SLOT_DURATION=60.0,
                                    CPU_P95_BASELINE_INTENSITY=20.0,
                                    CPU_P95_HIGH_INTENSITY=35.0,
                                    CPU_P95_TARGET_MIN=22.0,
                                    CPU_P95_TARGET_MAX=28.0,
                                    CPU_P95_EXCEEDANCE_TARGET=6.5,
                                    LOAD_CHECK_ENABLED=True,
                                    LOAD_THRESHOLD=0.6)
        self.patches.start()
        self.controller = CPUP95Controller(self.mock_storage)

    def tearDown(self):
        self.patches.stop()
        # Clean up test environment
        if 'PYTEST_CURRENT_TEST' in os.environ:
            del os.environ['PYTEST_CURRENT_TEST']

    def set_p95_and_clear_cache(self, value):
        """Helper to set P95 value and clear controller cache"""
        self.mock_storage.set_p95(value)
        self.controller._p95_cache = None
        self.controller._p95_cache_time = 0

    def test_first_slot_initialization(self):
        """Test that first slot is properly initialized"""
        # Controller should have initialized first slot during __init__
        self.assertIsNotNone(self.controller.current_slot_start)
        self.assertIsNotNone(self.controller.current_target_intensity)

        # With no P95 data, exceedance should be 0, so first slot should be high
        is_high, intensity = self.controller.should_run_high_slot(None)
        self.assertTrue(is_high)  # No exceedance budget used yet

    def test_slot_rollover_behavior(self):
        """Test single slot rollover"""
        original_start = self.controller.current_slot_start
        original_slots_recorded = self.controller.slots_recorded

        # Advance time beyond slot duration
        with patch('time.monotonic', return_value=original_start + 70):  # 70s > 60s
            is_high, intensity = self.controller.should_run_high_slot(None)

            # Should have advanced to new slot
            self.assertGreater(self.controller.current_slot_start, original_start)
            self.assertEqual(self.controller.slots_recorded, original_slots_recorded + 1)

    def test_safety_gating_with_high_load(self):
        """Test safety gating when load is high"""
        original_safety_count = self.controller.slots_skipped_safety

        # Force a new slot with high load
        with patch('time.monotonic', return_value=self.controller.current_slot_start + 70):
            is_high, intensity = self.controller.should_run_high_slot(0.8)  # > 0.6 threshold

            # Should be forced to baseline
            self.assertFalse(self.controller.current_slot_is_high)
            self.assertEqual(intensity, 20.0)  # BASELINE_INTENSITY
            self.assertEqual(self.controller.slots_skipped_safety, original_safety_count + 1)

    def test_safety_gating_respects_load_check_enabled(self):
        """Test safety gating only when LOAD_CHECK_ENABLED"""
        with patch.multiple(loadshaper, LOAD_CHECK_ENABLED=False):
            controller = CPUP95Controller(self.mock_storage)

            # Force new slot with high load but checking disabled
            with patch('time.monotonic', return_value=controller.current_slot_start + 70):
                is_high, intensity = controller.should_run_high_slot(0.8)

                # Should not be gated since LOAD_CHECK_ENABLED=False
                # (Exact behavior depends on exceedance budget)
                self.assertEqual(controller.slots_skipped_safety, 0)

    def test_exceedance_budget_control(self):
        """Test exceedance budget control behavior"""
        # Set up scenario where we want high exceedance
        self.set_p95_and_clear_cache(15.0)  # Low P95, should want high exceedance

        # Set up: controller initialized with current_slot_is_high=True by default
        # Reset history to all low slots and no recorded slots
        self.controller.slots_recorded = 0
        for i in range(self.controller.slot_history_size):
            self.controller.slot_history[i] = False  # All low slots

        # Set the initial current slot to low, so when rolled over it adds a low slot to history
        self.controller.current_slot_is_high = False

        # When slot rolls over: adds 1 low slot to history, exceedance = 0% < target (7%)
        # So next slot should be high
        with patch('time.monotonic', return_value=self.controller.current_slot_start + 70):
            is_high, intensity = self.controller.should_run_high_slot(None)
            self.assertTrue(is_high)

        # Simulate having many high slots (exceedance high)
        self.controller.slot_history[:10] = [True] * 10  # 10 high slots
        self.controller.slots_recorded = 10

        # New slot should be low (exceedance 100% > target)
        with patch('time.monotonic', return_value=self.controller.current_slot_start + 70):
            is_high, intensity = self.controller.should_run_high_slot(None)
            self.assertFalse(is_high)

    def test_ring_buffer_wraparound(self):
        """Test ring buffer wraparound behavior"""
        # Fill the entire ring buffer
        buffer_size = self.controller.slot_history_size
        self.controller.slot_history = [True] * buffer_size
        self.controller.slots_recorded = buffer_size
        self.controller.slot_history_index = 0

        # Record one more slot (should wrap around)
        self.controller._end_current_slot()

        # Should have wrapped to index 1
        self.assertEqual(self.controller.slot_history_index, 1)
        self.assertEqual(self.controller.slots_recorded, buffer_size)  # Shouldn't exceed size

    def test_get_current_exceedance_calculation(self):
        """Test exceedance percentage calculation"""
        # Set up known pattern: 3 high out of 10 slots = 30%
        self.controller.slot_history[:10] = [True, True, True] + [False] * 7
        self.controller.slots_recorded = 10

        exceedance = self.controller.get_current_exceedance()
        expected = (3 * 100.0) / 10  # 30%
        self.assertEqual(exceedance, expected)

        # Test with zero slots
        self.controller.slots_recorded = 0
        exceedance = self.controller.get_current_exceedance()
        self.assertEqual(exceedance, 0.0)

    def test_within_slot_behavior(self):
        """Test repeated calls within same slot return same result"""
        # First call
        is_high_1, intensity_1 = self.controller.should_run_high_slot(None)

        # Second call within same slot (time hasn't advanced)
        is_high_2, intensity_2 = self.controller.should_run_high_slot(None)

        # Should be identical
        self.assertEqual(is_high_1, is_high_2)
        self.assertEqual(intensity_1, intensity_2)

    def test_mark_current_slot_low_updates_current_state(self):
        """Test that mark_current_slot_low() updates current slot state during active slot"""
        # Set current slot as high (simulate controller decided this slot should be high)
        self.controller.current_slot_is_high = True
        self.controller.current_target_intensity = 35.0  # HIGH_INTENSITY from test setup

        # Verify initial state
        self.assertTrue(self.controller.current_slot_is_high, "Should start with high slot")
        self.assertEqual(self.controller.current_target_intensity, 35.0)

        # Now mark it as low (simulating main loop override due to load safety)
        self.controller.mark_current_slot_low()

        # Verify current state is updated
        self.assertFalse(self.controller.current_slot_is_high, "Current slot should be marked as low")
        self.assertEqual(self.controller.current_target_intensity, 20.0)  # BASELINE_INTENSITY from test setup

        # When the slot eventually ends and gets recorded, it will be recorded as low
        # Simulate slot ending
        self.controller._end_current_slot()

        # Verify it was recorded as low (not high)
        recorded_as_high = self.controller.slot_history[0]
        self.assertFalse(recorded_as_high, "Slot should be recorded as low after override")


class TestStatusReporting(unittest.TestCase):
    """Test status reporting and telemetry"""

    def setUp(self):
        self.mock_storage = MockMetricsStorage()
        self.patches = patch.multiple(loadshaper,
                                    CPU_P95_SLOT_DURATION=60.0,
                                    CPU_P95_BASELINE_INTENSITY=20.0,
                                    CPU_P95_HIGH_INTENSITY=35.0,
                                    CPU_P95_TARGET_MIN=22.0,
                                    CPU_P95_TARGET_MAX=28.0,
                                    CPU_P95_EXCEEDANCE_TARGET=6.5)
        self.patches.start()
        self.controller = CPUP95Controller(self.mock_storage)

    def tearDown(self):
        self.patches.stop()
        # Clean up test environment
        if 'PYTEST_CURRENT_TEST' in os.environ:
            del os.environ['PYTEST_CURRENT_TEST']

    def test_get_status_structure(self):
        """Test get_status returns proper structure"""
        self.mock_storage.set_p95(25.0)
        status = self.controller.get_status()

        # Check all required fields
        required_fields = ['state', 'cpu_p95', 'target_range', 'exceedance_pct',
                          'exceedance_target', 'current_slot_is_high', 'slot_remaining_sec',
                          'slots_recorded', 'slots_skipped_safety', 'target_intensity']

        for field in required_fields:
            self.assertIn(field, status)

    def test_target_range_formatting(self):
        """Test target range formatting"""
        status = self.controller.get_status()
        expected = "22.0-28.0%"
        self.assertEqual(status['target_range'], expected)

    def test_slot_remaining_calculation(self):
        """Test slot remaining time calculation"""
        start_time = time.monotonic()
        self.controller.current_slot_start = start_time

        with patch('time.monotonic', return_value=start_time + 30):  # 30s into slot
            status = self.controller.get_status()
            expected_remaining = 60.0 - 30.0  # 30s remaining
            self.assertEqual(status['slot_remaining_sec'], expected_remaining)

        # Test edge case where slot is overdue
        with patch('time.monotonic', return_value=start_time + 70):  # 70s into 60s slot
            status = self.controller.get_status()
            self.assertEqual(status['slot_remaining_sec'], 0.0)  # Should be 0, not negative


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and error conditions"""

    def setUp(self):
        # Set test environment to ensure deterministic behavior
        os.environ['PYTEST_CURRENT_TEST'] = 'test_edge_cases'
        self.mock_storage = MockMetricsStorage()

    def tearDown(self):
        # Clean up test environment
        if 'PYTEST_CURRENT_TEST' in os.environ:
            del os.environ['PYTEST_CURRENT_TEST']

    def test_extreme_slot_durations(self):
        """Test behavior with extreme slot durations"""
        # Very short slot duration
        with patch.multiple(loadshaper,
                           CPU_P95_SLOT_DURATION=1.0,  # 1 second
                           CPU_P95_BASELINE_INTENSITY=20.0,
                           CPU_P95_HIGH_INTENSITY=35.0,
                           CPU_P95_TARGET_MIN=22.0,
                           CPU_P95_TARGET_MAX=28.0,
                           CPU_P95_EXCEEDANCE_TARGET=6.5):
            controller = CPUP95Controller(self.mock_storage)
            # Should have huge buffer size (86400 slots)
            expected_size = 86400
            self.assertEqual(controller.slot_history_size, expected_size)

        # Very long slot duration
        with patch.multiple(loadshaper,
                           CPU_P95_SLOT_DURATION=3600.0,  # 1 hour
                           CPU_P95_BASELINE_INTENSITY=20.0,
                           CPU_P95_HIGH_INTENSITY=35.0,
                           CPU_P95_TARGET_MIN=22.0,
                           CPU_P95_TARGET_MAX=28.0,
                           CPU_P95_EXCEEDANCE_TARGET=6.5):
            controller = CPUP95Controller(self.mock_storage)
            # Should have small buffer size (24 slots)
            expected_size = 24
            self.assertEqual(controller.slot_history_size, expected_size)

    def test_baseline_exceeds_high_intensity(self):
        """Test when baseline intensity >= high intensity"""
        with patch.multiple(loadshaper,
                           CPU_P95_SLOT_DURATION=60.0,
                           CPU_P95_BASELINE_INTENSITY=40.0,  # Higher baseline
                           CPU_P95_HIGH_INTENSITY=35.0,     # Lower high
                           CPU_P95_TARGET_MIN=22.0,
                           CPU_P95_TARGET_MAX=28.0,
                           CPU_P95_EXCEEDANCE_TARGET=6.5):
            controller = CPUP95Controller(self.mock_storage)
            controller.state = 'REDUCING'

            # Even in REDUCING, should be floored at baseline
            intensity = controller.get_target_intensity()
            self.assertEqual(intensity, 40.0)  # Should be baseline, not high

    def test_database_exception_handling(self):
        """Test handling of database exceptions"""
        # Create controller first with normal storage
        with patch.multiple(loadshaper,
                           CPU_P95_SLOT_DURATION=60.0,
                           CPU_P95_BASELINE_INTENSITY=20.0,
                           CPU_P95_HIGH_INTENSITY=35.0,
                           CPU_P95_TARGET_MIN=22.0,
                           CPU_P95_TARGET_MAX=28.0,
                           CPU_P95_EXCEEDANCE_TARGET=6.5):
            controller = CPUP95Controller(self.mock_storage)

            # Now break the storage for subsequent calls
            def raise_exception(*args, **kwargs):
                raise Exception("Database error")

            self.mock_storage.get_percentile = raise_exception

            # Clear the cache to force database query
            controller._p95_cache = None
            controller._p95_cache_time = 0

            # Should propagate exception (caller's responsibility to handle)
            with self.assertRaises(Exception):
                controller.get_cpu_p95()

    def test_exact_equality_conditions(self):
        """Test behavior at exact threshold boundaries"""
        with patch.multiple(loadshaper,
                           CPU_P95_SLOT_DURATION=60.0,
                           CPU_P95_BASELINE_INTENSITY=20.0,
                           CPU_P95_HIGH_INTENSITY=35.0,
                           CPU_P95_TARGET_MIN=22.0,
                           CPU_P95_TARGET_MAX=28.0,
                           CPU_P95_EXCEEDANCE_TARGET=50.0):  # 50% for easier math
            controller = CPUP95Controller(self.mock_storage)

            # Set up exact 50% exceedance (5 high out of 10)
            controller.slot_history[:10] = [True] * 5 + [False] * 5
            controller.slots_recorded = 10

            # Current exceedance exactly equals target
            exceedance = controller.get_current_exceedance()
            self.assertEqual(exceedance, 50.0)

            # New slot decision with exact equality
            # Should prefer low slot (strict < comparison means high needs exceedance < target)
            controller.current_slot_is_high = None  # Reset
            with patch('time.monotonic', return_value=controller.current_slot_start + 70):
                controller._start_new_slot(None)
                self.assertFalse(controller.current_slot_is_high)

    def test_none_load_average_handling(self):
        """Test handling of None load average"""
        with patch.multiple(loadshaper,
                           CPU_P95_SLOT_DURATION=60.0,
                           CPU_P95_BASELINE_INTENSITY=20.0,
                           CPU_P95_HIGH_INTENSITY=35.0,
                           CPU_P95_TARGET_MIN=22.0,
                           CPU_P95_TARGET_MAX=28.0,
                           CPU_P95_EXCEEDANCE_TARGET=6.5,
                           LOAD_CHECK_ENABLED=True,
                           LOAD_THRESHOLD=0.6):
            controller = CPUP95Controller(self.mock_storage)

            # Should not trigger safety gating with None load
            original_safety_count = controller.slots_skipped_safety
            with patch('time.monotonic', return_value=controller.current_slot_start + 70):
                controller.should_run_high_slot(None)

            # Safety count shouldn't increase
            self.assertEqual(controller.slots_skipped_safety, original_safety_count)


class TestAdditionalEdgeCases(unittest.TestCase):
    """Test additional edge cases and missing scenarios"""

    def setUp(self):
        # Set test environment to ensure deterministic behavior
        os.environ['PYTEST_CURRENT_TEST'] = 'test_additional_edge_cases'

        self.mock_storage = MockMetricsStorage()
        self.patches = patch.multiple(loadshaper,
                                    CPU_P95_SLOT_DURATION=60.0,
                                    CPU_P95_BASELINE_INTENSITY=20.0,
                                    CPU_P95_HIGH_INTENSITY=35.0,
                                    CPU_P95_TARGET_MIN=22.0,
                                    CPU_P95_TARGET_MAX=28.0,
                                    CPU_P95_EXCEEDANCE_TARGET=6.5,
                                    LOAD_CHECK_ENABLED=True,
                                    LOAD_THRESHOLD=0.6)
        self.patches.start()
        self.controller = CPUP95Controller(self.mock_storage)

    def tearDown(self):
        self.patches.stop()
        # Clean up test environment
        if 'PYTEST_CURRENT_TEST' in os.environ:
            del os.environ['PYTEST_CURRENT_TEST']
        # Clean up test environment
        if 'PYTEST_CURRENT_TEST' in os.environ:
            del os.environ['PYTEST_CURRENT_TEST']

    def test_thread_safety_concurrent_status_access(self):
        """Test concurrent access to get_status() method"""
        import concurrent.futures

        # Set up some state
        self.controller._p95_cache = 25.0
        self.controller._p95_cache_time = time.monotonic()

        def get_status_worker():
            return self.controller.get_status()

        # Run multiple concurrent get_status() calls
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(get_status_worker) for _ in range(10)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        # All results should be valid status dictionaries
        for result in results:
            self.assertIsInstance(result, dict)
            self.assertIn('state', result)
            self.assertIn('cpu_p95', result)

    def test_p95_cache_thread_safety(self):
        """Test P95 cache access under concurrent conditions"""
        import concurrent.futures

        def access_p95_worker():
            return self.controller.get_cpu_p95()

        # Run concurrent P95 cache access
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(access_p95_worker) for _ in range(5)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        # Results should be consistent (either all None or all same value)
        unique_results = set(results)
        self.assertTrue(len(unique_results) <= 2)  # Allow None and one cached value

    def test_state_machine_handles_extreme_values(self):
        """Test that state machine can handle extreme P95 values without errors"""
        extreme_values = [0.0, 0.1, 99.9, 100.0, 150.0, -1.0]

        for cpu_p95 in extreme_values:
            with self.subTest(cpu_p95=cpu_p95):
                # Should not raise any exceptions
                initial_state = self.controller.state
                self.controller.update_state(cpu_p95)
                # State should be one of the valid states
                self.assertIn(self.controller.state, ['BUILDING', 'MAINTAINING', 'REDUCING'])

        # Test that very low values lead to BUILDING
        self.controller.update_state(0.0)
        self.assertEqual(self.controller.state, 'BUILDING')

        # Test that very high values lead to REDUCING
        self.controller.update_state(100.0)
        self.assertEqual(self.controller.state, 'REDUCING')

    def test_slot_history_wraparound_edge_cases(self):
        """Test slot history wraparound with exact boundary conditions"""
        buffer_size = self.controller.slot_history_size

        # Fill entire buffer with alternating pattern
        for i in range(buffer_size):
            self.controller.slot_history[i] = (i % 2 == 0)

        self.controller.slots_recorded = buffer_size
        self.controller.slot_history_index = 0  # At start for wraparound

        # Add one more slot (should wrap around to index 0)
        self.controller._end_current_slot()

        # Verify wraparound occurred correctly
        self.assertEqual(self.controller.slot_history_index, 1)
        self.assertEqual(self.controller.slots_recorded, buffer_size)  # Should stay at max

    def test_exceedance_calculation_precision(self):
        """Test exceedance calculation with precision edge cases"""
        # Set up exactly 100 slots for precise percentage calculations
        buffer_size = 100
        with patch.object(self.controller, 'slot_history_size', buffer_size):
            self.controller.slot_history = [False] * buffer_size
            self.controller.slots_recorded = buffer_size

            # Set exactly 6 high slots (6.0% exceedance)
            for i in range(6):
                self.controller.slot_history[i] = True

            exceedance = self.controller.get_current_exceedance()
            self.assertEqual(exceedance, 6.0)

    def test_mark_slot_low_when_already_low(self):
        """Test mark_current_slot_low when slot is already low"""
        # Set current slot as low initially
        self.controller.current_slot_is_high = False
        self.controller.current_target_intensity = 20.0

        # Record as low slot in history
        self.controller.slot_history[0] = False
        self.controller.slot_history_index = 1
        self.controller.slots_recorded = 1

        # Calling mark_current_slot_low should be safe/no-op
        self.controller.mark_current_slot_low()

        # State should remain unchanged
        self.assertFalse(self.controller.current_slot_is_high)
        self.assertEqual(self.controller.current_target_intensity, 20.0)
        self.assertFalse(self.controller.slot_history[0])

    def test_state_machine_with_extreme_values(self):
        """Test state machine handles extreme P95 values correctly."""
        # Test with 0% P95 (system completely idle)
        self.controller.update_state(0.0)
        self.assertEqual(self.controller.state, 'BUILDING')

        # Test with 100% P95 (system completely busy)
        self.controller.update_state(100.0)
        self.assertEqual(self.controller.state, 'REDUCING')

    def test_state_transitions_basic(self):
        """Test basic state transitions work correctly."""
        # Start in MAINTAINING
        self.controller.state = 'MAINTAINING'

        # Go below target range
        self.controller.update_state(15.0)  # Well below TARGET_MIN
        self.assertEqual(self.controller.state, 'BUILDING')

        # Go above target range
        self.controller.update_state(35.0)  # Well above TARGET_MAX
        self.assertEqual(self.controller.state, 'REDUCING')

        # Return to target range
        self.controller.update_state(25.0)  # Middle of target range
        self.assertEqual(self.controller.state, 'MAINTAINING')

    def test_none_p95_handling(self):
        """Test that None P95 values don't cause errors."""
        initial_state = self.controller.state

        # Calling with None should not change state
        self.controller.update_state(None)
        self.assertEqual(self.controller.state, initial_state)

        # Should not cause errors in get_target_intensity
        intensity = self.controller.get_target_intensity()
        self.assertIsInstance(intensity, (int, float))
        self.assertGreater(intensity, 0)

    def test_intensity_calculations_edge_cases(self):
        """Test intensity calculations with edge case states."""
        # Test BUILDING with very low P95 (should use aggressive boost)
        low_p95_storage = MockMetricsStorage()
        low_p95_storage.set_p95(5.0)
        building_controller = CPUP95Controller(low_p95_storage)
        building_controller.state = 'BUILDING'

        intensity = building_controller.get_target_intensity()
        # Should use aggressive boost since 5.0 < (22.0 - 5.0 = 17.0)
        expected = 35.0 + 8.0  # HIGH_INTENSITY + BUILD_AGGRESSIVE_INTENSITY_BOOST
        self.assertEqual(intensity, expected)

        # Test REDUCING with very high P95 (should use aggressive cut)
        high_p95_storage = MockMetricsStorage()
        high_p95_storage.set_p95(50.0)
        reducing_controller = CPUP95Controller(high_p95_storage)
        reducing_controller.state = 'REDUCING'

        intensity = reducing_controller.get_target_intensity()
        # Should be conservative (using REDUCE_AGGRESSIVE_INTENSITY_CUT)
        expected = 35.0 - 5.0  # HIGH_INTENSITY - REDUCE_AGGRESSIVE_INTENSITY_CUT
        self.assertEqual(intensity, expected)

    def test_exceedance_target_edge_cases(self):
        """Test exceedance target calculations with extreme P95 values."""
        # Test BUILDING with very low P95 (should use aggressive boost)
        low_p95_storage = MockMetricsStorage()
        low_p95_storage.set_p95(5.0)
        building_controller = CPUP95Controller(low_p95_storage)
        building_controller.state = 'BUILDING'

        target = building_controller.get_exceedance_target()
        # Should use aggressive exceedance boost: 6.5 + 4.0 = 10.5
        self.assertEqual(target, 10.5)  # BASE + BUILD_AGGRESSIVE_EXCEEDANCE_BOOST

        # Test REDUCING with very high P95
        high_p95_storage = MockMetricsStorage()
        high_p95_storage.set_p95(50.0)
        reducing_controller = CPUP95Controller(high_p95_storage)
        reducing_controller.state = 'REDUCING'

        target = reducing_controller.get_exceedance_target()
        # Should use very low exceedance for fast reduction
        self.assertEqual(target, 1.0)  # REDUCE_AGGRESSIVE_EXCEEDANCE_TARGET


if __name__ == '__main__':
    unittest.main()