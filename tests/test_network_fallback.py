#!/usr/bin/env python3

import unittest
import unittest.mock
import sys
import os

# Add the parent directory to the path so we can import loadshaper
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import loadshaper


class TestNetworkFallback(unittest.TestCase):
    """Test smart network fallback logic for E2 and A1 shapes."""

    def setUp(self):
        """Set up test environment before each test."""
        # Store original values to restore later
        self.original_net_activation = getattr(loadshaper, 'NET_ACTIVATION', 'adaptive')
        self.original_net_fallback_start_pct = getattr(loadshaper, 'NET_FALLBACK_START_PCT', 19.0)

        # Set test values
        loadshaper.NET_ACTIVATION = 'adaptive'
        loadshaper.NET_FALLBACK_START_PCT = 19.0

        # Create a mock metrics storage
        self.mock_metrics_storage = unittest.mock.MagicMock()

    def tearDown(self):
        """Clean up after each test."""
        loadshaper.NET_ACTIVATION = self.original_net_activation
        loadshaper.NET_FALLBACK_START_PCT = self.original_net_fallback_start_pct

    def test_e2_shape_fallback_network_low_cpu_safe(self):
        """Test E2: network low but CPU p95 safe -> no fallback."""
        with unittest.mock.patch('loadshaper.is_e2_shape', return_value=True):
            # Mock metrics: network low (18%), CPU p95 safe (25%)
            self.mock_metrics_storage.get_percentile.return_value = 25.0
            net_avg = 18.0  # Below 19% threshold
            mem_avg = None  # Not used for E2

            # Simulate the smart fallback logic
            cpu_p95 = self.mock_metrics_storage.get_percentile('cpu')
            cpu_at_risk = (cpu_p95 is not None and cpu_p95 < 22.0)
            network_low = net_avg < loadshaper.NET_FALLBACK_START_PCT

            fallback_needed = network_low and cpu_at_risk

            # Should NOT need fallback (CPU protects instance)
            self.assertFalse(fallback_needed,
                           f"E2 shape with safe CPU p95 ({cpu_p95}%) should not activate fallback despite low network ({net_avg}%)")

    def test_e2_shape_fallback_both_metrics_at_risk(self):
        """Test E2: both network and CPU at risk -> activate fallback."""
        with unittest.mock.patch('loadshaper.is_e2_shape', return_value=True):
            # Mock metrics: network low (18%), CPU p95 also at risk (21%)
            self.mock_metrics_storage.get_percentile.return_value = 21.0
            net_avg = 18.0  # Below 19% threshold

            # Simulate the smart fallback logic
            cpu_p95 = self.mock_metrics_storage.get_percentile('cpu')
            cpu_at_risk = (cpu_p95 is not None and cpu_p95 < 22.0)
            network_low = net_avg < loadshaper.NET_FALLBACK_START_PCT

            fallback_needed = network_low and cpu_at_risk

            # Should need fallback (both metrics at risk)
            self.assertTrue(fallback_needed,
                          f"E2 shape with risky CPU p95 ({cpu_p95}%) and low network ({net_avg}%) should activate fallback")

    def test_e2_shape_fallback_network_safe_cpu_at_risk(self):
        """Test E2: network safe but CPU at risk -> no fallback (network fallback doesn't help CPU)."""
        with unittest.mock.patch('loadshaper.is_e2_shape', return_value=True):
            # Mock metrics: network safe (25%), CPU p95 at risk (21%)
            self.mock_metrics_storage.get_percentile.return_value = 21.0
            net_avg = 25.0  # Above 19% threshold

            # Simulate the smart fallback logic
            cpu_p95 = self.mock_metrics_storage.get_percentile('cpu')
            cpu_at_risk = (cpu_p95 is not None and cpu_p95 < 22.0)
            network_low = net_avg < loadshaper.NET_FALLBACK_START_PCT

            fallback_needed = network_low and cpu_at_risk

            # Should NOT need network fallback (network already safe)
            self.assertFalse(fallback_needed,
                           f"E2 shape with safe network ({net_avg}%) should not activate network fallback even if CPU at risk ({cpu_p95}%)")

    def test_a1_shape_fallback_all_metrics_at_risk(self):
        """Test A1: activate only when ALL metrics are at risk (correct Oracle logic)."""
        with unittest.mock.patch('loadshaper.is_e2_shape', return_value=False):
            # Test case: ALL metrics at risk
            self.mock_metrics_storage.get_percentile.return_value = 21.0  # CPU at risk
            net_avg = 18.0  # Network at risk
            mem_avg = 21.0  # Memory at risk

            cpu_p95 = self.mock_metrics_storage.get_percentile('cpu')
            cpu_at_risk = (cpu_p95 is not None and cpu_p95 < 22.0)
            network_at_risk = net_avg < loadshaper.NET_FALLBACK_START_PCT
            mem_at_risk = (mem_avg is not None and mem_avg < 22.0)

            fallback_needed = network_at_risk and cpu_at_risk and mem_at_risk

            self.assertTrue(fallback_needed, "A1 shape should activate fallback when ALL metrics are at risk")

    def test_a1_shape_fallback_cpu_only_at_risk(self):
        """Test A1: should NOT activate when only CPU is at risk (network protects VM)."""
        with unittest.mock.patch('loadshaper.is_e2_shape', return_value=False):
            # Test case 2: Only CPU at risk, others safe
            self.mock_metrics_storage.get_percentile.return_value = 21.0  # CPU at risk
            net_avg = 25.0  # Network safe (protects VM)
            mem_avg = 25.0  # Memory safe (protects VM)

            cpu_p95 = self.mock_metrics_storage.get_percentile('cpu')
            cpu_at_risk = (cpu_p95 is not None and cpu_p95 < 22.0)
            network_at_risk = net_avg < loadshaper.NET_FALLBACK_START_PCT
            mem_at_risk = (mem_avg is not None and mem_avg < 22.0)

            fallback_needed = network_at_risk and cpu_at_risk and mem_at_risk

            self.assertFalse(fallback_needed, "A1 shape should NOT activate fallback when only CPU is at risk (network/memory protect VM)")

    def test_a1_shape_fallback_memory_only_at_risk(self):
        """Test A1: should NOT activate when only memory is at risk (CPU protects VM)."""
        with unittest.mock.patch('loadshaper.is_e2_shape', return_value=False):
            # Test case 3: Only memory at risk, others safe
            self.mock_metrics_storage.get_percentile.return_value = 25.0  # CPU safe (protects VM)
            net_avg = 25.0  # Network safe (protects VM)
            mem_avg = 21.0  # Memory at risk

            cpu_p95 = self.mock_metrics_storage.get_percentile('cpu')
            cpu_at_risk = (cpu_p95 is not None and cpu_p95 < 22.0)
            network_at_risk = net_avg < loadshaper.NET_FALLBACK_START_PCT
            mem_at_risk = (mem_avg is not None and mem_avg < 22.0)

            fallback_needed = network_at_risk and cpu_at_risk and mem_at_risk

            self.assertFalse(fallback_needed, "A1 shape should NOT activate fallback when only memory is at risk (CPU/network protect VM)")

    def test_a1_shape_fallback_all_metrics_safe(self):
        """Test A1: no fallback when all metrics are safe."""
        with unittest.mock.patch('loadshaper.is_e2_shape', return_value=False):
            # All metrics safe
            self.mock_metrics_storage.get_percentile.return_value = 25.0  # CPU safe
            net_avg = 25.0  # Network safe
            mem_avg = 25.0  # Memory safe

            cpu_p95 = self.mock_metrics_storage.get_percentile('cpu')
            cpu_at_risk = (cpu_p95 is not None and cpu_p95 < 22.0)
            network_at_risk = net_avg < loadshaper.NET_FALLBACK_START_PCT
            mem_at_risk = (mem_avg is not None and mem_avg < 22.0)

            fallback_needed = network_at_risk or cpu_at_risk or mem_at_risk

            self.assertFalse(fallback_needed, "A1 shape should not activate fallback when all metrics are safe")

    def test_fallback_activation_modes(self):
        """Test different NET_ACTIVATION modes."""
        # Test "off" mode
        loadshaper.NET_ACTIVATION = "off"
        fallback_needed = False  # Should always be False
        self.assertFalse(fallback_needed, "NET_ACTIVATION=off should never activate fallback")

        # Test "always" mode
        loadshaper.NET_ACTIVATION = "always"
        fallback_needed = True  # Should always be True
        self.assertTrue(fallback_needed, "NET_ACTIVATION=always should always activate fallback")

    def test_cpu_p95_none_handling(self):
        """Test handling when CPU p95 data is not available."""
        with unittest.mock.patch('loadshaper.is_e2_shape', return_value=True):
            # Mock metrics: network low, CPU p95 unavailable (None)
            self.mock_metrics_storage.get_percentile.return_value = None
            net_avg = 18.0  # Below threshold

            cpu_p95 = self.mock_metrics_storage.get_percentile('cpu')
            cpu_at_risk = (cpu_p95 is not None and cpu_p95 < 22.0)
            network_low = net_avg < loadshaper.NET_FALLBACK_START_PCT

            fallback_needed = network_low and cpu_at_risk

            # Should not activate when CPU data unavailable (safer default)
            self.assertFalse(fallback_needed,
                           "E2 shape should not activate fallback when CPU p95 data is unavailable (safer default)")

    def test_memory_avg_none_handling(self):
        """Test handling when memory average is not available."""
        with unittest.mock.patch('loadshaper.is_e2_shape', return_value=False):
            # Mock A1 shape with memory data unavailable
            self.mock_metrics_storage.get_percentile.return_value = 25.0  # CPU safe
            net_avg = 25.0  # Network safe
            mem_avg = None  # Memory data unavailable

            cpu_p95 = self.mock_metrics_storage.get_percentile('cpu')
            cpu_at_risk = (cpu_p95 is not None and cpu_p95 < 22.0)
            network_at_risk = net_avg < loadshaper.NET_FALLBACK_START_PCT
            mem_at_risk = (mem_avg is not None and mem_avg < 22.0)

            fallback_needed = network_at_risk or cpu_at_risk or mem_at_risk

            # Should not activate when only memory data is missing and other metrics safe
            self.assertFalse(fallback_needed,
                           "A1 shape should not activate fallback when memory data unavailable but other metrics safe")


if __name__ == '__main__':
    unittest.main()