#!/usr/bin/env python3

import unittest
import unittest.mock
import tempfile
import os
import sys
import socket
import ipaddress

# Add the parent directory to the path so we can import loadshaper
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import loadshaper


class TestNetworkHelperFunctions(unittest.TestCase):
    """Test helper functions added for network generation reliability."""

    def test_is_external_address_ipv4(self):
        """Test is_external_address with IPv4 addresses."""
        # External addresses (actual public IPs, not TEST-NET)
        self.assertTrue(loadshaper.is_external_address("1.2.3.4"))
        self.assertTrue(loadshaper.is_external_address("4.4.4.4"))
        self.assertTrue(loadshaper.is_external_address("208.67.222.222"))

        # Private addresses (RFC1918)
        self.assertFalse(loadshaper.is_external_address("192.168.1.1"))
        self.assertFalse(loadshaper.is_external_address("10.0.0.1"))
        self.assertFalse(loadshaper.is_external_address("172.16.0.1"))

        # Loopback
        self.assertFalse(loadshaper.is_external_address("127.0.0.1"))

        # Link-local
        self.assertFalse(loadshaper.is_external_address("169.254.1.1"))

        # Multicast
        self.assertFalse(loadshaper.is_external_address("224.0.0.1"))

        # CGN (Carrier Grade NAT)
        self.assertFalse(loadshaper.is_external_address("100.64.1.1"))

    def test_is_external_address_ipv6(self):
        """Test is_external_address with IPv6 addresses."""
        # External addresses
        self.assertTrue(loadshaper.is_external_address("2001:4860:4860::8888"))  # Google DNS
        self.assertTrue(loadshaper.is_external_address("2606:4700:4700::1111"))  # Cloudflare DNS

        # Private/local addresses
        self.assertFalse(loadshaper.is_external_address("::1"))  # Loopback
        self.assertFalse(loadshaper.is_external_address("fe80::1"))  # Link-local
        self.assertFalse(loadshaper.is_external_address("fc00::1"))  # ULA
        self.assertFalse(loadshaper.is_external_address("ff00::1"))  # Multicast
        self.assertFalse(loadshaper.is_external_address("2001:db8::1"))  # Documentation

    def test_is_external_address_invalid(self):
        """Test is_external_address with invalid inputs."""
        self.assertFalse(loadshaper.is_external_address("invalid"))
        self.assertFalse(loadshaper.is_external_address("256.256.256.256"))
        self.assertFalse(loadshaper.is_external_address(""))
        self.assertFalse(loadshaper.is_external_address("example.com"))

    def test_read_nic_tx_bytes_success(self):
        """Test read_nic_tx_bytes with valid interface."""
        # Mock /sys filesystem
        with tempfile.TemporaryDirectory() as temp_dir:
            interface_dir = os.path.join(temp_dir, "sys", "class", "net", "eth0", "statistics")
            os.makedirs(interface_dir)

            tx_bytes_file = os.path.join(interface_dir, "tx_bytes")
            with open(tx_bytes_file, "w") as f:
                f.write("12345678\n")

            # Mock the path in the function
            with unittest.mock.patch("builtins.open", unittest.mock.mock_open(read_data="12345678\n")):
                result = loadshaper.read_nic_tx_bytes("eth0")
                self.assertEqual(result, 12345678)

    def test_read_nic_tx_bytes_not_found(self):
        """Test read_nic_tx_bytes with non-existent interface."""
        result = loadshaper.read_nic_tx_bytes("nonexistent")
        self.assertIsNone(result)

    def test_read_nic_tx_bytes_invalid_data(self):
        """Test read_nic_tx_bytes with invalid file content."""
        with unittest.mock.patch("builtins.open", unittest.mock.mock_open(read_data="invalid\n")):
            result = loadshaper.read_nic_tx_bytes("eth0")
            self.assertIsNone(result)





class TestNetworkStateEnums(unittest.TestCase):
    """Test network state enumerations."""

    def test_network_state_values(self):
        """Test NetworkState enum values."""
        self.assertEqual(loadshaper.NetworkState.OFF.value, "OFF")
        self.assertEqual(loadshaper.NetworkState.INITIALIZING.value, "INITIALIZING")
        self.assertEqual(loadshaper.NetworkState.VALIDATING.value, "VALIDATING")
        self.assertEqual(loadshaper.NetworkState.ACTIVE_UDP.value, "ACTIVE_UDP")
        self.assertEqual(loadshaper.NetworkState.ACTIVE_TCP.value, "ACTIVE_TCP")
        self.assertEqual(loadshaper.NetworkState.ERROR.value, "ERROR")

    def test_peer_state_values(self):
        """Test PeerState enum values."""
        self.assertEqual(loadshaper.PeerState.UNVALIDATED.value, "UNVALIDATED")
        self.assertEqual(loadshaper.PeerState.VALID.value, "VALID")
        self.assertEqual(loadshaper.PeerState.INVALID.value, "INVALID")
        self.assertEqual(loadshaper.PeerState.DEGRADED.value, "DEGRADED")


if __name__ == '__main__':
    unittest.main()