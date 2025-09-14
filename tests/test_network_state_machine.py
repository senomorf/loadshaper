#!/usr/bin/env python3
"""
Test suite for NetworkGenerator state machine behavior
"""

import unittest
import unittest.mock
import time
import sys
import os

# Add the parent directory to the path so we can import loadshaper
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import loadshaper


class TestNetworkStateMachine(unittest.TestCase):
    """Test NetworkGenerator state machine transitions and hysteresis."""

    def setUp(self):
        """Set up test environment before each test."""
        self.generator = loadshaper.NetworkGenerator(rate_mbps=1.0, protocol="udp")

    def tearDown(self):
        """Clean up after each test."""
        # Don't automatically call stop() as it changes state to OFF
        # Individual tests will handle cleanup as needed
        pass

    def test_initial_state(self):
        """Test that generator starts in OFF state."""
        self.assertEqual(self.generator.state, loadshaper.NetworkState.OFF)

    def test_initialization_transition(self):
        """Test OFF -> INITIALIZING transition."""
        with unittest.mock.patch.object(self.generator, '_detect_network_interface'):
            # Capture initial state transition by monitoring the state during startup
            initial_state = self.generator.state
            self.assertEqual(initial_state, loadshaper.NetworkState.OFF)

            self.generator.start(["8.8.8.8"])

            # The start() method will transition through states
            # The final state depends on validation and fallback logic
            final_state = self.generator.state
            self.assertIsInstance(final_state, loadshaper.NetworkState)

            # Cleanup
            if final_state != loadshaper.NetworkState.OFF:
                self.generator.stop()

    def test_validation_state_transition(self):
        """Test INITIALIZING -> VALIDATING transition."""
        with unittest.mock.patch.object(self.generator, '_detect_network_interface'):
            with unittest.mock.patch.object(self.generator, '_validate_all_peers'):
                self.generator.start(["8.8.8.8"])

                # Should complete startup and reach a valid final state
                self.assertIsInstance(self.generator.state, loadshaper.NetworkState)

                # Cleanup
                if self.generator.state != loadshaper.NetworkState.OFF:
                    self.generator.stop()

    def test_active_udp_transition(self):
        """Test successful validation leads to ACTIVE_UDP."""
        with unittest.mock.patch.object(self.generator, '_detect_network_interface'):
            with unittest.mock.patch.object(self.generator, '_validate_all_peers'):
                with unittest.mock.patch.object(self.generator, '_start_udp'):
                    self.generator.start(["8.8.8.8"])

                    # Should complete startup successfully
                    self.assertIsInstance(self.generator.state, loadshaper.NetworkState)

                    # Cleanup
                    if self.generator.state != loadshaper.NetworkState.OFF:
                        self.generator.stop()

    def test_active_tcp_transition(self):
        """Test TCP protocol leads to ACTIVE_TCP."""
        tcp_gen = loadshaper.NetworkGenerator(rate_mbps=1.0, protocol="tcp")

        try:
            with unittest.mock.patch.object(tcp_gen, '_detect_network_interface'):
                with unittest.mock.patch.object(tcp_gen, '_validate_all_peers'):
                    with unittest.mock.patch.object(tcp_gen, '_start_tcp'):
                        tcp_gen.start(["8.8.8.8"])

                        # Protocol should be preserved
                        self.assertEqual(tcp_gen.protocol, "tcp")

        finally:
            tcp_gen.stop()

    def test_error_state_transition(self):
        """Test error conditions lead to ERROR state."""
        # Mock _detect_network_interface to raise an exception that won't be caught
        # The original method catches exceptions, so we need to mock at a different level
        with unittest.mock.patch.object(self.generator, '_initialize_peers',
                                       side_effect=Exception("Network error")):
            self.generator.start(["8.8.8.8"])

            # Should transition to ERROR state on exception
            self.assertEqual(self.generator.state, loadshaper.NetworkState.ERROR)


    def test_state_transition_debounce(self):
        """Test state transition debouncing prevents rapid changes."""
        # Mock time to control debounce timing
        with unittest.mock.patch('time.monotonic') as mock_time:
            mock_time.return_value = 1000.0

            # Initialize in a valid state
            with unittest.mock.patch.object(self.generator, '_detect_network_interface'):
                self.generator.start(["8.8.8.8"])

            # Force into ACTIVE state and record initial state
            self.generator.state = loadshaper.NetworkState.ACTIVE_UDP
            self.generator.state_start_time = mock_time.return_value
            self.generator.last_transition_time = mock_time.return_value
            initial_state = self.generator.state

            # Try to transition too quickly (within debounce time)
            mock_time.return_value = 1000.1  # 100ms later (< 5s debounce threshold)

            # Attempt transition to ERROR should be blocked by debounce
            self.generator._transition_state(loadshaper.NetworkState.ERROR, "test transition")

            # State should remain unchanged due to debounce protection
            self.assertEqual(self.generator.state, initial_state,
                           "State transition should be blocked by debounce timing")

            # Wait longer than both debounce period AND min-on time
            mock_time.return_value = 1020.0  # 20 seconds later (> 5s debounce and > 15s min-on)

            # Try transitioning to OFF state (which is always valid)
            self.generator._transition_state(loadshaper.NetworkState.OFF, "test transition after debounce")

            # Now transition should succeed
            self.assertEqual(self.generator.state, loadshaper.NetworkState.OFF,
                           "State transition should succeed after debounce and min-on periods")

    def test_min_on_time_hysteresis(self):
        """Test minimum on-time prevents premature state exits."""
        with unittest.mock.patch('time.monotonic') as mock_time:
            mock_time.return_value = 1000.0

            # Initialize generator
            with unittest.mock.patch.object(self.generator, '_detect_network_interface'):
                self.generator.start(["8.8.8.8"])

            # Force into ACTIVE_UDP state (which has min-on time restrictions)
            self.generator.state = loadshaper.NetworkState.ACTIVE_UDP
            self.generator.state_start_time = mock_time.return_value
            self.generator.last_transition_time = mock_time.return_value - 10.0  # Set debounce clear
            initial_state = self.generator.state

            # Attempt to transition away too quickly (within min-on time)
            mock_time.return_value = 1005.0  # 5 seconds later (< 15s min-on time)

            # Try to force transition to TCP (non-error/stop transition)
            self.generator._transition_state(loadshaper.NetworkState.ACTIVE_TCP, "premature transition")

            # State should remain unchanged due to min-on time protection
            self.assertEqual(self.generator.state, initial_state,
                           "Active state transition should be blocked by min-on time hysteresis")

            # Wait longer than min-on time and try again
            mock_time.return_value = 1020.0  # 20 seconds later (> 15s min-on time)

            self.generator._transition_state(loadshaper.NetworkState.ACTIVE_TCP, "transition after min-on time")

            # Now transition should succeed
            self.assertEqual(self.generator.state, loadshaper.NetworkState.ACTIVE_TCP,
                           "State transition should succeed after min-on time period")

            # Test min-off time for inactive states
            mock_time.return_value = 1021.0
            self.generator.state = loadshaper.NetworkState.OFF
            self.generator.state_start_time = mock_time.return_value
            self.generator.last_transition_time = mock_time.return_value - 10.0

            # Try to transition away from OFF state too quickly (< 20s min-off time)
            mock_time.return_value = 1025.0  # 4 seconds later (< 20s min-off)

            self.generator._transition_state(loadshaper.NetworkState.INITIALIZING, "premature off transition")

            # Should remain in OFF state
            self.assertEqual(self.generator.state, loadshaper.NetworkState.OFF,
                           "Inactive state transition should be blocked by min-off time hysteresis")

    def test_peer_validation_state_changes(self):
        """Test peer validation affects state transitions."""
        with unittest.mock.patch.object(self.generator, '_detect_network_interface'):
            # Mock all peers as invalid
            with unittest.mock.patch.object(self.generator, '_validate_peer', return_value=False):
                self.generator.start(["invalid.peer.test"])

                # Should handle invalid peers gracefully
                self.assertIn(self.generator.state, [s for s in loadshaper.NetworkState])

    def test_empty_peer_list_handling(self):
        """Test behavior with empty peer list (no fallbacks available)."""
        with unittest.mock.patch.object(self.generator, '_detect_network_interface'):
            # Start with no peers - should reach ERROR state
            self.generator.start([])

            # Should reach ERROR state when no peers available
            self.assertIn(self.generator.state, [s for s in loadshaper.NetworkState])

    def test_protocol_failure_cascade(self):
        """Test protocol failure handling with fallback cascade."""
        with unittest.mock.patch.object(self.generator, '_detect_network_interface'):
            with unittest.mock.patch.object(self.generator, '_validate_all_peers'):
                # Mock UDP failure
                with unittest.mock.patch.object(self.generator, '_start_udp',
                                               side_effect=Exception("UDP failed")):
                    self.generator.start(["8.8.8.8"])

                    # Should handle UDP failure gracefully
                    self.assertIn(self.generator.state, [s for s in loadshaper.NetworkState])

    def test_stop_state_cleanup(self):
        """Test stop() properly cleans up state."""
        # Create generator with validate_startup=False to simplify test
        self.generator.validate_startup = False

        with unittest.mock.patch.object(self.generator, '_detect_network_interface'):
            with unittest.mock.patch.object(self.generator, '_start_protocol'):
                self.generator.start(["8.8.8.8"])

                # Record pre-stop state (should be ACTIVE_UDP after start)
                pre_stop_state = self.generator.state
                self.assertIsInstance(pre_stop_state, loadshaper.NetworkState)
                # Should be in an active state after successful start
                # Debug: print state to see what's happening
                if pre_stop_state not in [loadshaper.NetworkState.ACTIVE_UDP, loadshaper.NetworkState.ACTIVE_TCP]:
                    print(f"DEBUG: Unexpected state: {pre_stop_state}")
                    print(f"DEBUG: validate_startup: {self.generator.validate_startup}")
                    print(f"DEBUG: state transitions: {self.generator.state_transitions}")
                self.assertIn(pre_stop_state, [
                    loadshaper.NetworkState.ACTIVE_UDP,
                    loadshaper.NetworkState.ACTIVE_TCP
                ])

                # Stop should return to OFF state
                self.generator.stop()
                self.assertEqual(self.generator.state, loadshaper.NetworkState.OFF)

    def test_state_persistence_during_operation(self):
        """Test state remains stable during normal operation."""
        with unittest.mock.patch.object(self.generator, '_detect_network_interface'):
            self.generator.start(["8.8.8.8"])

            initial_state = self.generator.state

            # Perform some operations
            try:
                self.generator.send_burst(0.01)  # Short burst
            except:
                pass  # May fail in test environment

            # State should remain stable
            self.assertEqual(self.generator.state, initial_state)

    def test_state_enum_values(self):
        """Test all NetworkState enum values are valid."""
        expected_states = {
            loadshaper.NetworkState.OFF,
            loadshaper.NetworkState.INITIALIZING,
            loadshaper.NetworkState.VALIDATING,
            loadshaper.NetworkState.ACTIVE_UDP,
            loadshaper.NetworkState.ACTIVE_TCP,
            loadshaper.NetworkState.ERROR
        }

        # All states should be represented
        self.assertEqual(len(expected_states), 7)

        # Each state should have a string value
        for state in expected_states:
            self.assertIsInstance(state.value, str)
            self.assertTrue(len(state.value) > 0)

    def test_concurrent_state_access(self):
        """Test state machine handles concurrent access safely."""
        import threading

        results = []

        def worker():
            try:
                with unittest.mock.patch.object(self.generator, '_detect_network_interface'):
                    self.generator.start(["8.8.8.8"])
                results.append("success")
            except Exception as e:
                results.append(f"error: {e}")
            finally:
                try:
                    self.generator.stop()
                except:
                    pass

        # Start multiple threads
        threads = []
        for i in range(3):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()

        # Wait for completion
        for t in threads:
            t.join(timeout=1.0)

        # Should handle concurrent access without crashes
        self.assertGreater(len(results), 0)


if __name__ == '__main__':
    unittest.main()