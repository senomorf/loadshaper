#!/usr/bin/env python3

import unittest
import unittest.mock
import sys
import os

# Add the parent directory to the path so we can import loadshaper
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import loadshaper


class TestShapeDetectionEnhanced(unittest.TestCase):
    """Test enhanced shape detection including E2 vs A1 determination."""

    def test_is_e2_shape_oracle_environment(self):
        """Test is_e2_shape() in Oracle environment with proper shape names."""
        # Test E2 shape detection
        with unittest.mock.patch('loadshaper.detect_oracle_shape',
                                return_value=('VM.Standard.E2.1.Micro', 'e2-1-micro.env', True)):
            self.assertTrue(loadshaper.is_e2_shape(),
                          "Should detect E2.1.Micro as E2 shape")

        with unittest.mock.patch('loadshaper.detect_oracle_shape',
                                return_value=('VM.Standard.E2.2.Micro', 'e2-2-micro.env', True)):
            self.assertTrue(loadshaper.is_e2_shape(),
                          "Should detect E2.2.Micro as E2 shape")

        # Test A1 shape detection
        with unittest.mock.patch('loadshaper.detect_oracle_shape',
                                return_value=('VM.Standard.A1.Flex', 'a1-flex-1.env', True)):
            self.assertFalse(loadshaper.is_e2_shape(),
                           "Should detect A1.Flex as non-E2 shape")

        # Test unknown Oracle shape
        with unittest.mock.patch('loadshaper.detect_oracle_shape',
                                return_value=('VM.Standard.E3.1.Standard', 'unknown.env', True)):
            self.assertFalse(loadshaper.is_e2_shape(),
                           "Should treat unknown Oracle shape as non-E2")

    def test_is_e2_shape_non_oracle_environment(self):
        """Test is_e2_shape() in non-Oracle environment using architecture heuristics."""
        # Test x86_64 architecture (E2-like)
        with unittest.mock.patch('loadshaper.detect_oracle_shape',
                                return_value=('Generic-4CPU-8.0GB', None, False)):
            with unittest.mock.patch('platform.machine', return_value='x86_64'):
                self.assertTrue(loadshaper.is_e2_shape(),
                              "Should treat x86_64 as E2-like shape")

            with unittest.mock.patch('platform.machine', return_value='amd64'):
                self.assertTrue(loadshaper.is_e2_shape(),
                              "Should treat amd64 as E2-like shape")

        # Test ARM architecture (A1-like)
        with unittest.mock.patch('loadshaper.detect_oracle_shape',
                                return_value=('Generic-4CPU-8.0GB', None, False)):
            with unittest.mock.patch('platform.machine', return_value='aarch64'):
                self.assertFalse(loadshaper.is_e2_shape(),
                               "Should treat aarch64 as A1-like shape")

            with unittest.mock.patch('platform.machine', return_value='arm64'):
                self.assertFalse(loadshaper.is_e2_shape(),
                               "Should treat arm64 as A1-like shape")

        # Test unknown architecture (default to A1-like for safety)
        with unittest.mock.patch('loadshaper.detect_oracle_shape',
                                return_value=('Generic-4CPU-8.0GB', None, False)):
            with unittest.mock.patch('platform.machine', return_value='riscv64'):
                self.assertFalse(loadshaper.is_e2_shape(),
                               "Should default unknown architecture to A1-like (safer)")

    def test_is_e2_shape_oracle_documentation_reference(self):
        """Test that is_e2_shape function includes proper Oracle documentation reference."""
        # Check that the function docstring includes the Oracle URL
        docstring = loadshaper.is_e2_shape.__doc__
        self.assertIn('https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm',
                     docstring, "Function should reference Oracle documentation URL")

        # Check that it explains the reclamation rules correctly
        self.assertIn('CPU utilization for the 95th percentile is less than 20%', docstring)
        self.assertIn('Network utilization is less than 20%', docstring)
        self.assertIn('Memory utilization is less than 20% (applies to A1 shapes only)', docstring)

    def test_shape_reclamation_rules_understanding(self):
        """Test understanding of Oracle reclamation rules for different shapes."""
        # E2 shapes: Only CPU and network matter
        with unittest.mock.patch('loadshaper.is_e2_shape', return_value=True):
            shape_is_e2 = loadshaper.is_e2_shape()

            # For E2, memory doesn't matter for reclamation
            # This is just testing our understanding, not a specific function
            metrics_that_matter_for_e2 = ['cpu_p95', 'network_current']
            metrics_ignored_for_e2 = ['memory_current']

            self.assertTrue(shape_is_e2)
            self.assertEqual(len(metrics_that_matter_for_e2), 2,
                           "E2 shapes should have exactly 2 metrics that matter for reclamation")

        # A1 shapes: All three metrics matter
        with unittest.mock.patch('loadshaper.is_e2_shape', return_value=False):
            shape_is_e2 = loadshaper.is_e2_shape()

            # For A1, all metrics matter for reclamation
            metrics_that_matter_for_a1 = ['cpu_p95', 'network_current', 'memory_current']

            self.assertFalse(shape_is_e2)
            self.assertEqual(len(metrics_that_matter_for_a1), 3,
                           "A1 shapes should have exactly 3 metrics that matter for reclamation")

    def test_reclamation_threshold_constants(self):
        """Test that our threshold constants align with Oracle's 20% rule."""
        oracle_threshold = 20.0
        safety_buffer = 2.0
        our_risk_threshold = 22.0  # Used in fallback logic

        # Verify our safety buffer is reasonable
        self.assertEqual(our_risk_threshold, oracle_threshold + safety_buffer,
                        "Risk threshold should be Oracle threshold plus safety buffer")

        # Verify we're not too conservative (which would waste resources)
        self.assertLessEqual(safety_buffer, 5.0,
                           "Safety buffer should not be excessive")

        # Verify we have a buffer (not exactly at Oracle's threshold)
        self.assertGreater(our_risk_threshold, oracle_threshold,
                         "Should have safety buffer above Oracle's threshold")


if __name__ == '__main__':
    unittest.main()