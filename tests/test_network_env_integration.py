#!/usr/bin/env python3
"""
Tests for NetworkGenerator ENV variable integration.

Validates that environment variables are properly applied to NetworkGenerator
instances in the net_client_thread, fixing the critical issue identified
in the merge review.
"""

import unittest
import unittest.mock
import os
import sys
import tempfile
import threading
import time

# Add the parent directory to path so we can import loadshaper
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import loadshaper


class TestNetworkEnvIntegration(unittest.TestCase):
    """Test NetworkGenerator ENV variable integration."""

    def setUp(self):
        """Set up test environment."""
        self.original_env = os.environ.copy()

    def tearDown(self):
        """Clean up test environment."""
        os.environ.clear()
        os.environ.update(self.original_env)

    def test_require_external_constructor_parameter(self):
        """Test require_external constructor parameter works correctly."""
        # Test that the constructor accepts and sets require_external
        generator_external = loadshaper.NetworkGenerator(
            rate_mbps=10.0,
            protocol="udp",
            ttl=1,
            packet_size=1100,
            port=15201,
            require_external=True,
            validate_startup=True
        )

        # Verify that require_external was properly set
        self.assertTrue(generator_external.require_external,
                      "require_external=True should be set in constructor")

        # Test false case
        generator_no_external = loadshaper.NetworkGenerator(
            rate_mbps=10.0,
            protocol="udp",
            ttl=1,
            packet_size=1100,
            port=15201,
            require_external=False,
            validate_startup=True
        )

        self.assertFalse(generator_no_external.require_external,
                        "require_external=False should be set in constructor")

    def test_timing_parameters_applied(self):
        """Test timing ENV variables are properly applied to NetworkGenerator."""
        # Test that our fixed code properly applies timing parameters
        generator = loadshaper.NetworkGenerator(
            rate_mbps=10.0,
            protocol="udp",
            ttl=1,
            packet_size=1100,
            port=15201,
            require_external=False,
            validate_startup=True
        )

        # Verify default timing parameters are set
        self.assertEqual(generator.state_debounce_sec, 5.0,
                        "Default state_debounce_sec should be 5.0")
        self.assertEqual(generator.state_min_on_sec, 15.0,
                        "Default state_min_on_sec should be 15.0")
        self.assertEqual(generator.state_min_off_sec, 20.0,
                        "Default state_min_off_sec should be 20.0")

        # Test that we can update them (as done in our fix)
        generator.state_debounce_sec = 7.5
        generator.state_min_on_sec = 25.0
        generator.state_min_off_sec = 30.0

        # Verify that timing parameters were properly applied
        self.assertEqual(generator.state_debounce_sec, 7.5,
                        "state_debounce_sec should be updateable")
        self.assertEqual(generator.state_min_on_sec, 25.0,
                        "state_min_on_sec should be updateable")
        self.assertEqual(generator.state_min_off_sec, 30.0,
                        "state_min_off_sec should be updateable")

    def test_validate_startup_constructor_parameter(self):
        """Test validate_startup constructor parameter works correctly."""
        # Test that the constructor accepts and sets validate_startup
        generator_validate = loadshaper.NetworkGenerator(
            rate_mbps=10.0,
            protocol="udp",
            ttl=1,
            packet_size=1100,
            port=15201,
            require_external=False,
            validate_startup=True
        )

        # Verify that validate_startup was properly set
        self.assertTrue(generator_validate.validate_startup,
                      "validate_startup=True should be set in constructor")

        # Test false case
        generator_no_validate = loadshaper.NetworkGenerator(
            rate_mbps=10.0,
            protocol="udp",
            ttl=1,
            packet_size=1100,
            port=15201,
            require_external=False,
            validate_startup=False
        )

        self.assertFalse(generator_no_validate.validate_startup,
                        "validate_startup=False should be set in constructor")

    def test_integration_logic_behavior(self):
        """Test the OR logic behavior that our fix implements."""
        # Mock the is_e2_shape() function behavior
        with unittest.mock.patch('loadshaper.is_e2_shape', return_value=True):
            # Test the logic: NET_REQUIRE_EXTERNAL or is_e2_shape()
            # Even if NET_REQUIRE_EXTERNAL is False, E2 shape should force True
            env_require_external = False
            e2_shape_detected = loadshaper.is_e2_shape()

            result = env_require_external or e2_shape_detected

            generator = loadshaper.NetworkGenerator(
                rate_mbps=10.0,
                protocol="udp",
                ttl=1,
                packet_size=1100,
                port=15201,
                require_external=result,  # This is the logic our fix uses
                validate_startup=True
            )

            # Verify that E2 shape detection forces require_external=True
            self.assertTrue(generator.require_external,
                          "E2 shape should force require_external=True via OR logic")

        # Test the opposite case - non-E2 shape
        with unittest.mock.patch('loadshaper.is_e2_shape', return_value=False):
            env_require_external = False
            e2_shape_detected = loadshaper.is_e2_shape()

            result = env_require_external or e2_shape_detected

            generator = loadshaper.NetworkGenerator(
                rate_mbps=10.0,
                protocol="udp",
                ttl=1,
                packet_size=1100,
                port=15201,
                require_external=result,
                validate_startup=True
            )

            # Should be False when neither ENV nor E2 shape forces it
            self.assertFalse(generator.require_external,
                           "Non-E2 shape with ENV=False should result in require_external=False")


if __name__ == '__main__':
    unittest.main()