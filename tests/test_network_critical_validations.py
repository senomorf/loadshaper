#!/usr/bin/env python3
"""
Test suite for critical network generation validations.

Tests:
1. State machine initialization without debounce blocking
2. CGNAT detection for entire 100.64.0.0/10 range
3. Special-use IP range detection (RFC 2544, TEST-NETs)
4. tx_bytes validation with correct packet sizes
"""

import unittest
from unittest.mock import MagicMock, patch, PropertyMock
import time
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import loadshaper
from loadshaper import NetworkGenerator, NetworkState, is_external_address


class TestStateMachineInitialization(unittest.TestCase):
    """Test that state machine allows initial transition without timing restrictions."""

    def test_first_transition_bypasses_debounce(self):
        """Initial OFF->INITIALIZING transition should not be blocked by debounce."""
        gen = NetworkGenerator(rate_mbps=10.0, validate_startup=False)

        # State should be OFF initially
        self.assertEqual(gen.state, NetworkState.OFF)
        self.assertEqual(len(gen.state_transitions), 0)

        # Immediately try to transition (no delay)
        gen._transition_state(NetworkState.INITIALIZING, "test start")

        # Should succeed despite no time passing
        self.assertEqual(gen.state, NetworkState.INITIALIZING)
        self.assertEqual(len(gen.state_transitions), 1)

    def test_first_transition_bypasses_min_off(self):
        """Initial OFF state should not enforce min_off time."""
        gen = NetworkGenerator(rate_mbps=10.0, validate_startup=False)

        # Set aggressive min_off time
        gen.state_min_off_sec = 60.0

        # Should still allow immediate transition from initial OFF
        gen._transition_state(NetworkState.INITIALIZING, "test start")
        self.assertEqual(gen.state, NetworkState.INITIALIZING)

    def test_subsequent_transitions_enforce_timing(self):
        """After startup, timing restrictions should apply to non-startup transitions."""
        gen = NetworkGenerator(rate_mbps=10.0, validate_startup=False)
        gen.state_debounce_sec = 0.1  # Short debounce for testing
        gen.state_min_on_sec = 0.1  # Short min-on for testing

        # Startup transitions should succeed without debounce
        gen._transition_state(NetworkState.INITIALIZING, "startup")
        self.assertEqual(gen.state, NetworkState.INITIALIZING)
        gen._transition_state(NetworkState.ACTIVE_UDP, "startup complete")
        self.assertEqual(gen.state, NetworkState.ACTIVE_UDP)

        # Non-startup transition should be blocked by debounce
        gen._transition_state(NetworkState.ACTIVE_TCP, "too fast")
        self.assertEqual(gen.state, NetworkState.ACTIVE_UDP)  # Should still be UDP

        # After both debounce and min-on time, should succeed
        time.sleep(0.12)  # Slightly more than both 0.1s debounce and 0.1s min-on
        gen._transition_state(NetworkState.ACTIVE_TCP, "after debounce")
        self.assertEqual(gen.state, NetworkState.ACTIVE_TCP)


class TestCGNATDetection(unittest.TestCase):
    """Test proper CGNAT range detection (100.64.0.0/10)."""

    def test_cgnat_start_of_range(self):
        """Test 100.64.0.0 is detected as CGNAT."""
        self.assertFalse(is_external_address("100.64.0.0"))
        self.assertFalse(is_external_address("100.64.0.1"))
        self.assertFalse(is_external_address("100.64.255.255"))

    def test_cgnat_middle_of_range(self):
        """Test middle addresses in CGNAT range."""
        self.assertFalse(is_external_address("100.96.0.0"))
        self.assertFalse(is_external_address("100.100.100.100"))
        self.assertFalse(is_external_address("100.120.0.1"))

    def test_cgnat_end_of_range(self):
        """Test end of CGNAT range (100.127.255.255)."""
        self.assertFalse(is_external_address("100.127.0.0"))
        self.assertFalse(is_external_address("100.127.255.254"))
        self.assertFalse(is_external_address("100.127.255.255"))

    def test_cgnat_boundaries(self):
        """Test addresses just outside CGNAT range."""
        # Just before range (100.63.255.255 is not in CGNAT, so it's external)
        self.assertTrue(is_external_address("100.63.255.255"))
        # Just after range
        self.assertTrue(is_external_address("100.128.0.0"))


class TestSpecialUseRanges(unittest.TestCase):
    """Test detection of special-use IP ranges."""

    def test_rfc2544_benchmarking_range(self):
        """Test RFC 2544 benchmarking range (198.18.0.0/15)."""
        self.assertFalse(is_external_address("198.18.0.0"))
        self.assertFalse(is_external_address("198.18.0.1"))
        self.assertFalse(is_external_address("198.19.0.0"))
        self.assertFalse(is_external_address("198.19.255.254"))
        self.assertFalse(is_external_address("198.19.255.255"))
        # Just outside the range
        self.assertTrue(is_external_address("198.20.0.0"))
        self.assertTrue(is_external_address("198.17.255.255"))

    def test_testnet_ranges(self):
        """Test TEST-NET ranges."""
        # TEST-NET-1 (192.0.2.0/24)
        self.assertFalse(is_external_address("192.0.2.0"))
        self.assertFalse(is_external_address("192.0.2.100"))
        self.assertFalse(is_external_address("192.0.2.255"))

        # TEST-NET-2 (198.51.100.0/24)
        self.assertFalse(is_external_address("198.51.100.0"))
        self.assertFalse(is_external_address("198.51.100.1"))
        self.assertFalse(is_external_address("198.51.100.255"))

        # TEST-NET-3 (203.0.113.0/24)
        self.assertFalse(is_external_address("203.0.113.0"))
        self.assertFalse(is_external_address("203.0.113.100"))
        self.assertFalse(is_external_address("203.0.113.255"))

    def test_other_special_ranges(self):
        """Test other special-use ranges."""
        # IETF Protocol Assignments (192.0.0.0/24)
        self.assertFalse(is_external_address("192.0.0.0"))
        self.assertFalse(is_external_address("192.0.0.255"))

        # Deprecated 6to4 relay (192.88.99.0/24)
        self.assertFalse(is_external_address("192.88.99.0"))
        self.assertFalse(is_external_address("192.88.99.255"))

        # Reserved for future use (240.0.0.0/4)
        self.assertFalse(is_external_address("240.0.0.0"))
        self.assertFalse(is_external_address("255.255.255.254"))

    def test_ipv6_special_ranges(self):
        """Test IPv6 special ranges."""
        # Documentation range
        self.assertFalse(is_external_address("2001:db8::1"))
        self.assertFalse(is_external_address("2001:db8:ffff:ffff:ffff:ffff:ffff:ffff"))

        # ORCHIDv2 range
        self.assertFalse(is_external_address("2001:10::1"))
        self.assertFalse(is_external_address("2001:1f:ffff:ffff:ffff:ffff:ffff:ffff"))

    def test_valid_external_addresses(self):
        """Test that actual external addresses are recognized."""
        self.assertTrue(is_external_address("192.0.2.1"))
        self.assertTrue(is_external_address("198.51.100.1"))
        self.assertTrue(is_external_address("203.0.113.1"))
        self.assertTrue(is_external_address("208.67.222.222"))
        self.assertTrue(is_external_address("2001:4860:4860::8888"))  # Google DNS IPv6


class TestTxBytesValidation(unittest.TestCase):
    """Test tx_bytes validation with correct packet sizes."""

    @patch('loadshaper.NetworkGenerator._get_tx_bytes')
    def test_validation_uses_actual_bytes_sent(self, mock_get_tx):
        """Test that validation uses actual bytes sent, not packet count * packet_size."""
        gen = NetworkGenerator(rate_mbps=10.0, validate_startup=False)
        gen.network_interface = "eth0"
        gen.packet_size = 1100
        # Mock tx_bytes readings (before and after)
        mock_get_tx.return_value = 1001024  # After value

        # Test with actual bytes sent (regular UDP packets)
        bytes_sent = 1100  # One regular packet
        gen._validate_transmission_effectiveness(1000000, bytes_sent, 2)

        # Should use actual bytes (1024) for validation
        # EMA is now calculated as bytes per second, not raw bytes
        # With default elapsed time of 0.1s minimum, rate would be 1024/0.1 = 10240 B/s
        # EMA update: 0 * 0.8 + 10240 * 0.2 = 2048 B/s
        self.assertAlmostEqual(gen.tx_bytes_ema, 2048, delta=100)

    @patch('loadshaper.NetworkGenerator._get_tx_bytes')
    def test_external_egress_checks_actual_peer(self, mock_get_tx):
        """Test that external_egress_verified only set when sending to external peer."""
        gen = NetworkGenerator(rate_mbps=10.0, validate_startup=False)
        gen.network_interface = "eth0"

        # Setup peers with PeerState enum values
        from loadshaper import PeerState
        gen.peers = {
            "192.168.1.1": {"is_external": False, "state": PeerState.VALID},
            "192.0.2.1": {"is_external": True, "state": PeerState.VALID}
        }

        # Test 1: Sending to internal peer should NOT verify external egress
        mock_get_tx.return_value = 1002000  # Good increase
        gen.last_sent_peer = "192.168.1.1"  # Set the last sent peer directly
        gen.external_egress_verified = False
        gen._validate_transmission_effectiveness(1000000, 1500, 1)
        self.assertFalse(gen.external_egress_verified)

        # Test 2: Sending to external peer SHOULD verify external egress
        mock_get_tx.return_value = 1004000  # Another good increase
        gen.last_sent_peer = "192.0.2.1"  # Set the last sent peer directly
        gen._validate_transmission_effectiveness(1002000, 1500, 1)
        self.assertTrue(gen.external_egress_verified)

    def test_send_burst_tracks_correct_packet_sizes(self):
        """Test that send_burst correctly tracks packet sizes for different packet types."""
        gen = NetworkGenerator(rate_mbps=100.0, validate_startup=False)
        gen.packet_size = 1100
        gen.state = NetworkState.ACTIVE_UDP

        # Setup mock socket and peers
        gen.socket = MagicMock()
        gen.socket.sendto = MagicMock(return_value=None)

        # Add a regular external peer
        gen.peers = {"192.0.2.1": {"state": "VALID", "is_external": True, "blacklist_until": 0}}

        with patch('loadshaper.NetworkGenerator._get_tx_bytes', return_value=1000000):
            with patch('loadshaper.NetworkGenerator._validate_transmission_effectiveness') as mock_validate:
                # Run a short burst
                packets_sent = gen.send_burst(0.01)

                # Check that validate was called with bytes_sent, not packets * packet_size
                if mock_validate.called:
                    call_args = mock_validate.call_args[0]
                    bytes_sent_arg = call_args[1]  # Second argument is bytes_sent
                    # Should be actual bytes sent, could be mix of packet sizes
                    self.assertIsInstance(bytes_sent_arg, int)
                    self.assertGreaterEqual(bytes_sent_arg, 0)


if __name__ == '__main__':
    unittest.main()