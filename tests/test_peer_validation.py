#!/usr/bin/env python3
"""
Test suite for NetworkGenerator peer validation functionality.

Tests the comprehensive peer validation system including DNS validation,
TCP handshake validation, reputation scoring, blacklisting, and recovery.
"""

import unittest
import unittest.mock
import socket
import time
import sys
import os

# Add the parent directory to the path so we can import loadshaper
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import loadshaper


class TestPeerValidation(unittest.TestCase):
    """Test NetworkGenerator peer validation functionality."""

    def setUp(self):
        """Set up test environment before each test."""
        self.generator = loadshaper.NetworkGenerator(rate_mbps=1.0, protocol="udp")

    def tearDown(self):
        """Clean up after each test."""
        if hasattr(self.generator, 'state') and self.generator.state != loadshaper.NetworkState.OFF:
            self.generator.stop()




    def test_tcp_peer_validation_success(self):
        """Test successful TCP peer validation."""
        with unittest.mock.patch('socket.getaddrinfo') as mock_getaddrinfo:
            with unittest.mock.patch('socket.socket') as mock_socket_class:
                # Mock address resolution
                mock_getaddrinfo.return_value = [
                    (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                     '', ('1.2.3.4', self.generator.port))
                ]

                # Mock successful TCP connection
                mock_socket = mock_socket_class.return_value

                result = self.generator._validate_generic_peer('example.com')

                self.assertTrue(result)
                mock_socket.connect.assert_called_once_with(('1.2.3.4', self.generator.port))
                mock_socket.close.assert_called()

    def test_tcp_peer_validation_connection_refused(self):
        """Test TCP peer validation with connection refused."""
        with unittest.mock.patch('socket.getaddrinfo') as mock_getaddrinfo:
            with unittest.mock.patch('socket.socket') as mock_socket_class:
                # Mock address resolution
                mock_getaddrinfo.return_value = [
                    (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                     '', ('1.2.3.4', self.generator.port))
                ]

                # Mock connection refused
                mock_socket = mock_socket_class.return_value
                mock_socket.connect.side_effect = socket.error("Connection refused")

                result = self.generator._validate_generic_peer('example.com')

                self.assertFalse(result)
                mock_socket.close.assert_called()

    def test_tcp_peer_validation_multiple_addresses(self):
        """Test TCP peer validation tries multiple addresses."""
        with unittest.mock.patch('socket.getaddrinfo') as mock_getaddrinfo:
            with unittest.mock.patch('socket.socket') as mock_socket_class:
                # Mock multiple address resolution
                mock_getaddrinfo.return_value = [
                    (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                     '', ('1.2.3.4', self.generator.port)),
                    (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                     '', ('1.2.3.5', self.generator.port))
                ]

                # Mock first connection fails, second succeeds
                mock_socket_instances = [unittest.mock.MagicMock(), unittest.mock.MagicMock()]
                mock_socket_class.side_effect = mock_socket_instances

                mock_socket_instances[0].connect.side_effect = socket.error("First failed")
                # Second connection succeeds (no exception)

                result = self.generator._validate_generic_peer('example.com')

                self.assertTrue(result)
                # Both sockets should be closed
                mock_socket_instances[0].close.assert_called()
                mock_socket_instances[1].close.assert_called()


    def test_validate_peer_uses_tcp_validation(self):
        """Test _validate_peer uses TCP validation for all peers."""
        with unittest.mock.patch.object(self.generator, '_validate_generic_peer') as mock_generic_validate:
            mock_generic_validate.return_value = True

            result = self.generator._validate_peer('8.8.8.8')

            self.assertTrue(result)
            mock_generic_validate.assert_called_once_with('8.8.8.8')

    def test_peer_reputation_scoring_valid_peer(self):
        """Test reputation scoring for valid peer."""
        # Initialize peers with test peer
        self.generator.peers = {
            '8.8.8.8': {
                'state': loadshaper.PeerState.UNVALIDATED,
                'reputation': 60.0,
                'last_attempt': 0.0,
                'successes': 0,
                'failures': 0,
                'blacklist_until': 0.0
            }
        }

        initial_reputation = self.generator.peers['8.8.8.8']['reputation']

        # Test successful peer recording
        self.generator._record_peer_success('8.8.8.8')

        peer_info = self.generator.peers['8.8.8.8']
        self.assertGreater(peer_info['reputation'], initial_reputation)  # Should increase
        self.assertEqual(peer_info['successes'], 1)
        self.assertEqual(peer_info['failures'], 0)

    def test_peer_reputation_scoring_invalid_peer(self):
        """Test reputation scoring for invalid peer."""
        # Initialize peers with test peer
        self.generator.peers = {
            '8.8.8.8': {
                'state': loadshaper.PeerState.VALID,
                'reputation': 80.0,
                'last_attempt': 0.0,
                'successes': 5,
                'failures': 0,
                'blacklist_until': 0.0
            }
        }

        initial_reputation = self.generator.peers['8.8.8.8']['reputation']

        # Test failed peer recording
        self.generator._record_peer_failure('8.8.8.8', "Connection timeout")

        peer_info = self.generator.peers['8.8.8.8']
        self.assertLess(peer_info['reputation'], initial_reputation)  # Should decrease
        self.assertEqual(peer_info['successes'], 5)
        self.assertEqual(peer_info['failures'], 1)

    def test_peer_blacklisting_threshold(self):
        """Test peer gets blacklisted when reputation falls below threshold."""
        # Initialize peer with low reputation
        self.generator.peers = {
            'bad.peer.com': {
                'state': loadshaper.PeerState.VALID,
                'reputation': 24.0,  # Close to blacklist threshold (20.0), failure penalty is 5.0
                'last_attempt': 0.0,
                'successes': 1,
                'failures': 5,
                'blacklist_until': 0.0
            }
        }

        # Record enough failures to push reputation below blacklist threshold
        self.generator._record_peer_failure('bad.peer.com', "Connection failed")

        peer_info = self.generator.peers['bad.peer.com']
        self.assertLess(peer_info['reputation'], self.generator.REPUTATION_BLACKLIST_THRESHOLD)
        self.assertGreater(peer_info['blacklist_until'], time.time())  # Should be blacklisted
        self.assertEqual(peer_info['state'], loadshaper.PeerState.INVALID)

    def test_peer_recovery_from_blacklist(self):
        """Test peer recovery from blacklist after timeout."""
        current_time = time.time()

        # Initialize blacklisted peer with expired blacklist
        self.generator.peers = {
            'recovered.peer.com': {
                'state': loadshaper.PeerState.INVALID,
                'reputation': 15.0,  # Below threshold
                'last_attempt': current_time - 3600,
                'successes': 0,
                'failures': 10,
                'blacklist_until': current_time - 100  # Expired blacklist
            }
        }

        with unittest.mock.patch.object(self.generator, '_validate_peer', return_value=True):
            self.generator._check_peer_recovery()

            peer_info = self.generator.peers['recovered.peer.com']
            self.assertEqual(peer_info['blacklist_until'], 0.0)  # Should clear blacklist
            # Recovery process should have attempted validation

    def test_peer_recovery_respects_interval(self):
        """Test peer recovery respects check interval."""
        # Set recent last check time
        self.generator._last_recovery_check = time.time() - 30  # 30 seconds ago

        # Initialize blacklisted peer
        self.generator.peers = {
            'test.peer.com': {
                'state': loadshaper.PeerState.INVALID,
                'reputation': 15.0,
                'last_attempt': 0.0,
                'successes': 0,
                'failures': 10,
                'blacklist_until': time.time() - 100  # Expired
            }
        }

        with unittest.mock.patch.object(self.generator, '_validate_peer') as mock_validate:
            self.generator._check_peer_recovery()

            # Should not attempt validation due to interval limit (60 seconds)
            mock_validate.assert_not_called()

    def test_peer_validation_during_startup(self):
        """Test peer validation is called during startup."""
        with unittest.mock.patch.object(self.generator, '_detect_network_interface'):
            with unittest.mock.patch.object(self.generator, '_validate_peer', return_value=True) as mock_validate:
                with unittest.mock.patch.object(self.generator, '_start_udp'):
                    self.generator.start(['8.8.8.8', '1.1.1.1'])

                    # Should validate all provided peers
                    self.assertEqual(mock_validate.call_count, 2)
                    mock_validate.assert_any_call('8.8.8.8')
                    mock_validate.assert_any_call('1.1.1.1')


    def test_ipv6_peer_validation(self):
        """Test IPv6 peer validation support."""
        with unittest.mock.patch('socket.getaddrinfo') as mock_getaddrinfo:
            with unittest.mock.patch('socket.socket') as mock_socket_class:
                # Mock IPv6 address resolution
                mock_getaddrinfo.return_value = [
                    (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                     '', ('2001:db8::1', self.generator.port, 0, 0))
                ]

                mock_socket = mock_socket_class.return_value

                result = self.generator._validate_generic_peer('ipv6.example.com')

                self.assertTrue(result)
                mock_socket.connect.assert_called_once_with(('2001:db8::1', self.generator.port, 0, 0))

    def test_validation_timeout_configuration(self):
        """Test validation timeout constants are properly configured."""
        self.assertGreater(self.generator.TCP_VALIDATION_TIMEOUT, 0)
        self.assertLessEqual(self.generator.TCP_VALIDATION_TIMEOUT, 10)  # Reasonable timeout

    def test_reputation_constants(self):
        """Test reputation system constants are properly configured."""
        self.assertEqual(self.generator.REPUTATION_INITIAL_NEUTRAL, 50.0)
        self.assertEqual(self.generator.REPUTATION_BLACKLIST_THRESHOLD, 20.0)

        # Thresholds should make sense
        self.assertGreater(self.generator.REPUTATION_INITIAL_NEUTRAL, self.generator.REPUTATION_BLACKLIST_THRESHOLD)


if __name__ == '__main__':
    unittest.main()