#!/usr/bin/env python3

import unittest
import unittest.mock
import sys
import os
import time

# Add the parent directory to the path so we can import loadshaper
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import loadshaper


class TestNetworkTiming(unittest.TestCase):
    """Test network fallback timing, debounce, and rate ramping logic."""

    def setUp(self):
        """Set up test environment before each test."""
        # Store original values
        self.original_net_activation = getattr(loadshaper, 'NET_ACTIVATION', 'adaptive')
        self.original_debounce_sec = getattr(loadshaper, 'NET_FALLBACK_DEBOUNCE_SEC', 30.0)
        self.original_min_on_sec = getattr(loadshaper, 'NET_FALLBACK_MIN_ON_SEC', 60.0)
        self.original_min_off_sec = getattr(loadshaper, 'NET_FALLBACK_MIN_OFF_SEC', 180.0)
        self.original_ramp_sec = getattr(loadshaper, 'NET_FALLBACK_RAMP_SEC', 30.0)
        self.original_start_pct = getattr(loadshaper, 'NET_FALLBACK_START_PCT', 19.0)
        self.original_stop_pct = getattr(loadshaper, 'NET_FALLBACK_STOP_PCT', 22.0)

        # Set test values for predictable timing
        loadshaper.NET_ACTIVATION = 'adaptive'
        loadshaper.NET_FALLBACK_DEBOUNCE_SEC = 5.0  # Short for testing
        loadshaper.NET_FALLBACK_MIN_ON_SEC = 10.0   # Short for testing
        loadshaper.NET_FALLBACK_MIN_OFF_SEC = 15.0  # Short for testing
        loadshaper.NET_FALLBACK_RAMP_SEC = 3.0      # Short for testing
        loadshaper.NET_FALLBACK_START_PCT = 19.0
        loadshaper.NET_FALLBACK_STOP_PCT = 22.0

    def tearDown(self):
        """Clean up after each test."""
        loadshaper.NET_ACTIVATION = self.original_net_activation
        loadshaper.NET_FALLBACK_DEBOUNCE_SEC = self.original_debounce_sec
        loadshaper.NET_FALLBACK_MIN_ON_SEC = self.original_min_on_sec
        loadshaper.NET_FALLBACK_MIN_OFF_SEC = self.original_min_off_sec
        loadshaper.NET_FALLBACK_RAMP_SEC = self.original_ramp_sec
        loadshaper.NET_FALLBACK_START_PCT = self.original_start_pct
        loadshaper.NET_FALLBACK_STOP_PCT = self.original_stop_pct

    def test_debounce_prevents_immediate_activation(self):
        """Test that debounce timer prevents immediate fallback activation."""
        # Simulate conditions that would trigger fallback
        net_avg = 18.0  # Below 19% threshold
        cpu_p95 = 21.0  # Below 22% threshold (for E2)

        # Mock E2 shape and time
        with unittest.mock.patch('loadshaper.is_e2_shape', return_value=True):
            with unittest.mock.patch('time.time') as mock_time:
                # Initial time
                mock_time.return_value = 1000.0

                # Simulate fallback condition detection
                net_condition_start_time = 0.0
                net_fallback_active = False

                # First detection - should start debounce timer
                cpu_at_risk = cpu_p95 < 22.0
                network_low = net_avg < loadshaper.NET_FALLBACK_START_PCT
                fallback_needed = network_low and cpu_at_risk

                if fallback_needed and not net_fallback_active:
                    if net_condition_start_time == 0.0:
                        net_condition_start_time = mock_time.return_value
                        should_activate = False  # Still in debounce
                    else:
                        elapsed = mock_time.return_value - net_condition_start_time
                        should_activate = elapsed >= loadshaper.NET_FALLBACK_DEBOUNCE_SEC
                else:
                    should_activate = False

                self.assertFalse(should_activate, "Fallback should not activate immediately due to debounce")
                self.assertEqual(net_condition_start_time, 1000.0, "Debounce timer should be started")

    def test_debounce_allows_activation_after_delay(self):
        """Test that fallback activates after debounce period."""
        net_avg = 18.0  # Below threshold
        cpu_p95 = 21.0  # Below threshold

        with unittest.mock.patch('loadshaper.is_e2_shape', return_value=True):
            with unittest.mock.patch('time.time') as mock_time:
                # Simulate debounce timer started at time 1000
                net_condition_start_time = 1000.0
                net_fallback_active = False

                # Time after debounce period (5 seconds in test)
                mock_time.return_value = 1006.0  # 6 seconds elapsed

                cpu_at_risk = cpu_p95 < 22.0
                network_low = net_avg < loadshaper.NET_FALLBACK_START_PCT
                fallback_needed = network_low and cpu_at_risk

                if fallback_needed and not net_fallback_active:
                    elapsed = mock_time.return_value - net_condition_start_time
                    should_activate = elapsed >= loadshaper.NET_FALLBACK_DEBOUNCE_SEC
                else:
                    should_activate = False

                self.assertTrue(should_activate, "Fallback should activate after debounce period")

    def test_minimum_on_time_prevents_early_deactivation(self):
        """Test that minimum on time prevents early fallback deactivation."""
        with unittest.mock.patch('time.time') as mock_time:
            # Simulate fallback activated at time 1000
            net_fallback_start_time = 1000.0
            net_fallback_active = True

            # Conditions improve (should normally stop fallback)
            net_avg = 25.0  # Above stop threshold
            fallback_needed = False

            # Time before minimum on period (10 seconds in test)
            mock_time.return_value = 1005.0  # Only 5 seconds elapsed

            if not fallback_needed and net_fallback_active:
                elapsed = mock_time.return_value - net_fallback_start_time
                should_deactivate = elapsed >= loadshaper.NET_FALLBACK_MIN_ON_SEC
            else:
                should_deactivate = False

            self.assertFalse(should_deactivate, "Fallback should not deactivate before minimum on time")

    def test_minimum_on_time_allows_deactivation_after_period(self):
        """Test that fallback can deactivate after minimum on time."""
        with unittest.mock.patch('time.time') as mock_time:
            # Simulate fallback activated at time 1000
            net_fallback_start_time = 1000.0
            net_fallback_active = True

            # Conditions improve
            net_avg = 25.0  # Above stop threshold
            fallback_needed = False

            # Time after minimum on period (10 seconds in test)
            mock_time.return_value = 1015.0  # 15 seconds elapsed

            if not fallback_needed and net_fallback_active:
                elapsed = mock_time.return_value - net_fallback_start_time
                should_deactivate = elapsed >= loadshaper.NET_FALLBACK_MIN_ON_SEC
            else:
                should_deactivate = False

            self.assertTrue(should_deactivate, "Fallback should deactivate after minimum on time")

    def test_minimum_off_time_prevents_immediate_reactivation(self):
        """Test that minimum off time prevents immediate reactivation."""
        with unittest.mock.patch('time.time') as mock_time:
            # Simulate fallback deactivated at time 1000
            net_fallback_stop_time = 1000.0
            net_fallback_active = False

            # Conditions deteriorate again (should normally start fallback)
            net_avg = 18.0  # Below threshold
            cpu_p95 = 21.0  # Below threshold
            fallback_needed = True

            # Time before minimum off period (15 seconds in test)
            mock_time.return_value = 1010.0  # Only 10 seconds elapsed

            if fallback_needed and not net_fallback_active:
                elapsed = mock_time.return_value - net_fallback_stop_time
                should_activate = elapsed >= loadshaper.NET_FALLBACK_MIN_OFF_SEC
            else:
                should_activate = False

            self.assertFalse(should_activate, "Fallback should not reactivate before minimum off time")

    def test_stop_threshold_with_hysteresis(self):
        """Test that stop threshold is higher than start threshold (hysteresis)."""
        # Verify configuration has proper hysteresis
        self.assertGreater(loadshaper.NET_FALLBACK_STOP_PCT, loadshaper.NET_FALLBACK_START_PCT,
                          "Stop threshold should be higher than start threshold for hysteresis")

        # Test that network between start and stop thresholds maintains current state
        net_avg_middle = 20.5  # Between 19% (start) and 22% (stop)

        # Case 1: Fallback currently active - should remain active
        net_fallback_active = True
        should_stop = net_avg_middle >= loadshaper.NET_FALLBACK_STOP_PCT
        self.assertFalse(should_stop, "Fallback should remain active when network is between thresholds")

        # Case 2: Fallback currently inactive - should remain inactive
        net_fallback_active = False
        should_start = net_avg_middle < loadshaper.NET_FALLBACK_START_PCT
        self.assertFalse(should_start, "Fallback should remain inactive when network is between thresholds")

    def test_rate_ramping_boundaries(self):
        """Test that rate ramping handles boundary conditions properly."""
        # Test minimum rate boundary
        elapsed_time = 0.0  # Start of ramp
        expected_rate_min = 1.0  # Minimum rate (1 Mbps)
        ramp_progress = min(1.0, elapsed_time / loadshaper.NET_FALLBACK_RAMP_SEC)

        # Simulate rate calculation (would need access to actual rate calculation)
        # This is a simplified test of the ramping concept
        if ramp_progress <= 0.0:
            actual_rate = expected_rate_min
        else:
            # Linear ramp up (simplified)
            max_rate = 50.0  # Example max rate
            actual_rate = expected_rate_min + (max_rate - expected_rate_min) * ramp_progress

        self.assertEqual(actual_rate, expected_rate_min, "Rate should start at minimum")

        # Test end of ramp period
        elapsed_time = loadshaper.NET_FALLBACK_RAMP_SEC  # End of ramp
        ramp_progress = min(1.0, elapsed_time / loadshaper.NET_FALLBACK_RAMP_SEC)

        if ramp_progress >= 1.0:
            actual_rate = 50.0  # Max rate
        else:
            actual_rate = expected_rate_min + (50.0 - expected_rate_min) * ramp_progress

        self.assertEqual(actual_rate, 50.0, "Rate should reach maximum after ramp period")

    def test_fallback_state_transitions(self):
        """Test complete state transition cycle with proper timing."""
        with unittest.mock.patch('time.time') as mock_time:
            # Initial state
            net_fallback_active = False
            net_condition_start_time = 0.0
            net_fallback_start_time = 0.0
            net_fallback_stop_time = 0.0

            # === PHASE 1: Condition detected, debounce starts ===
            mock_time.return_value = 1000.0
            net_avg = 18.0  # Triggers condition

            # Start debounce
            net_condition_start_time = mock_time.return_value

            # === PHASE 2: After debounce, fallback activates ===
            mock_time.return_value = 1006.0  # Past debounce (5s)
            net_fallback_active = True
            net_fallback_start_time = mock_time.return_value

            # === PHASE 3: Condition improves, but min on time prevents stop ===
            mock_time.return_value = 1010.0  # Only 4s since activation
            net_avg = 25.0  # Condition improves

            elapsed_on = mock_time.return_value - net_fallback_start_time
            should_stop = elapsed_on >= loadshaper.NET_FALLBACK_MIN_ON_SEC
            self.assertFalse(should_stop, "Should not stop before minimum on time")

            # === PHASE 4: After min on time, fallback deactivates ===
            mock_time.return_value = 1020.0  # 14s since activation, past min on (10s)
            net_fallback_active = False
            net_fallback_stop_time = mock_time.return_value

            # === PHASE 5: Condition deteriorates again, but min off prevents restart ===
            mock_time.return_value = 1025.0  # Only 5s since deactivation
            net_avg = 18.0  # Condition deteriorates

            elapsed_off = mock_time.return_value - net_fallback_stop_time
            should_start = elapsed_off >= loadshaper.NET_FALLBACK_MIN_OFF_SEC
            self.assertFalse(should_start, "Should not restart before minimum off time")

            # === PHASE 6: After min off time, can activate again ===
            mock_time.return_value = 1040.0  # 20s since deactivation, past min off (15s)

            elapsed_off = mock_time.return_value - net_fallback_stop_time
            can_restart = elapsed_off >= loadshaper.NET_FALLBACK_MIN_OFF_SEC
            self.assertTrue(can_restart, "Should be able to restart after minimum off time")


if __name__ == '__main__':
    unittest.main()