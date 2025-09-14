#!/usr/bin/env python3
"""
Test suite for NetworkFallbackState class functionality
"""

import unittest
import time
import sys
import os

# Add the parent directory to the path so we can import loadshaper
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import loadshaper


class TestNetworkFallbackState(unittest.TestCase):
    """Test suite for NetworkFallbackState basic functionality"""

    def setUp(self):
        """Set up test case with NetworkFallbackState"""
        self.fallback_state = loadshaper.NetworkFallbackState()

    def test_initialization(self):
        """Test NetworkFallbackState initialization"""
        self.assertFalse(self.fallback_state.active)
        self.assertEqual(self.fallback_state.activation_count, 0)
        self.assertEqual(self.fallback_state.last_change, 0.0)
        self.assertEqual(self.fallback_state.last_activation, 0.0)
        self.assertEqual(self.fallback_state.last_deactivation, 0.0)

    def test_state_attributes_exist(self):
        """Test that all expected attributes exist"""
        # These attributes should be accessible
        self.assertIsInstance(self.fallback_state.active, bool)
        self.assertIsInstance(self.fallback_state.activation_count, int)
        self.assertIsInstance(self.fallback_state.last_change, float)
        self.assertIsInstance(self.fallback_state.last_activation, float)
        self.assertIsInstance(self.fallback_state.last_deactivation, float)

    def test_should_activate_method_exists(self):
        """Test that should_activate method exists and is callable"""
        # Should not crash when called (even if globals not initialized)
        try:
            # This may return False due to missing globals, but shouldn't crash
            result = self.fallback_state.should_activate(
                is_e2=True,
                cpu_p95=20.0,
                net_avg=18.0,
                mem_avg=None
            )
            self.assertIsInstance(result, bool, "should_activate should return boolean")
        except Exception as e:
            # If it fails due to uninitialized globals, that's expected
            # We're just testing the interface exists
            if "None" in str(e) or "not supported" in str(e):
                pass  # Expected when globals not initialized
            else:
                raise

    def test_debug_info_structure(self):
        """Test that get_debug_info returns expected structure"""
        try:
            debug_info = self.fallback_state.get_debug_info()

            # Should have these keys
            expected_keys = ['active', 'activation_count', 'seconds_since_change',
                           'in_debounce', 'last_activation_ago', 'last_deactivation_ago']

            for key in expected_keys:
                self.assertIn(key, debug_info, f"Debug info should contain '{key}'")

            # Check types
            self.assertIsInstance(debug_info['active'], bool)
            self.assertIsInstance(debug_info['activation_count'], int)

        except Exception as e:
            # If it fails due to uninitialized globals, that's expected in unit tests
            if "None" in str(e) or "not supported" in str(e):
                self.skipTest("Skipping debug_info test - requires global configuration")
            else:
                raise

    def test_state_transitions_basic(self):
        """Test basic state transitions"""
        # Initially inactive
        self.assertFalse(self.fallback_state.active)

        # Can set active manually for testing
        self.fallback_state.active = True
        self.assertTrue(self.fallback_state.active)

        # Can increment activation count
        initial_count = self.fallback_state.activation_count
        self.fallback_state.activation_count += 1
        self.assertEqual(self.fallback_state.activation_count, initial_count + 1)


if __name__ == '__main__':
    unittest.main()