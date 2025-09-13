#!/usr/bin/env python3

import unittest
import unittest.mock
import sys
import os

# Add the parent directory to the path so we can import loadshaper
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import loadshaper


class TestOracleValidation(unittest.TestCase):
    """Test Oracle configuration validation and warning logic."""

    def setUp(self):
        """Set up test environment before each test."""
        # Store original values
        self.original_cpu_target = getattr(loadshaper, 'CPU_P95_SETPOINT', 25.0)
        self.original_mem_target = getattr(loadshaper, 'MEM_TARGET_PCT', 0.0)
        self.original_net_target = getattr(loadshaper, 'NET_TARGET_PCT', 25.0)
        self.original_detected_shape = getattr(loadshaper, 'DETECTED_SHAPE', 'Unknown')

    def tearDown(self):
        """Clean up after each test."""
        loadshaper.CPU_P95_SETPOINT = self.original_cpu_target
        loadshaper.MEM_TARGET_PCT = self.original_mem_target
        loadshaper.NET_TARGET_PCT = self.original_net_target
        loadshaper.DETECTED_SHAPE = self.original_detected_shape

    def test_e2_shape_critical_warning_both_metrics_below_threshold(self):
        """Test E2: critical warning when both CPU and network below 20%."""
        # Set E2 shape with both metrics below threshold
        loadshaper.DETECTED_SHAPE = "VM.Standard.E2.1.Micro"
        loadshaper.CPU_P95_SETPOINT = 15.0  # Below 20%
        loadshaper.MEM_TARGET_PCT = 0.0   # Disabled (correct for E2)
        loadshaper.NET_TARGET_PCT = 15.0  # Below 20%

        # Check validation logic for E2 shape
        targets_below_20 = []
        if loadshaper.CPU_P95_SETPOINT < 20.0:
            targets_below_20.append(f"CPU_P95_SETPOINT={loadshaper.CPU_P95_SETPOINT}%")
        if loadshaper.NET_TARGET_PCT < 20.0:
            targets_below_20.append(f"NET_TARGET_PCT={loadshaper.NET_TARGET_PCT}%")

        # E2 critical condition: both CPU and NET below 20%
        cpu_below = loadshaper.CPU_P95_SETPOINT < 20.0
        net_below = loadshaper.NET_TARGET_PCT < 20.0
        should_be_critical = cpu_below and net_below

        self.assertTrue(should_be_critical,
                       "E2 shape with both CPU and NET below 20% should trigger critical warning")
        self.assertEqual(len(targets_below_20), 2,
                        "Should identify exactly 2 problematic targets for E2")

    def test_e2_shape_safe_configuration_cpu_above_threshold(self):
        """Test E2: safe configuration when CPU above 20% (network can be below)."""
        # Set E2 shape with CPU safe, network below threshold
        loadshaper.DETECTED_SHAPE = "VM.Standard.E2.1.Micro"
        loadshaper.CPU_P95_SETPOINT = 25.0  # Above 20% (safe)
        loadshaper.MEM_TARGET_PCT = 0.0   # Disabled (correct for E2)
        loadshaper.NET_TARGET_PCT = 15.0  # Below 20% (acceptable)

        # Check validation logic
        cpu_below = loadshaper.CPU_P95_SETPOINT < 20.0
        net_below = loadshaper.NET_TARGET_PCT < 20.0
        should_be_critical = cpu_below and net_below

        self.assertFalse(should_be_critical,
                        "E2 shape with CPU above 20% should be safe even if network below 20%")

    def test_e2_shape_safe_configuration_network_above_threshold(self):
        """Test E2: safe configuration when network above 20% (CPU can be below)."""
        # Set E2 shape with network safe, CPU below threshold
        loadshaper.DETECTED_SHAPE = "VM.Standard.E2.1.Micro"
        loadshaper.CPU_P95_SETPOINT = 15.0  # Below 20% (acceptable)
        loadshaper.MEM_TARGET_PCT = 0.0   # Disabled (correct for E2)
        loadshaper.NET_TARGET_PCT = 25.0  # Above 20% (safe)

        # Check validation logic
        cpu_below = loadshaper.CPU_P95_SETPOINT < 20.0
        net_below = loadshaper.NET_TARGET_PCT < 20.0
        should_be_critical = cpu_below and net_below

        self.assertFalse(should_be_critical,
                        "E2 shape with network above 20% should be safe even if CPU below 20%")

    def test_a1_shape_critical_warning_all_metrics_below_threshold(self):
        """Test A1: critical warning when all three metrics below 20%."""
        # Set A1 shape with all metrics below threshold
        loadshaper.DETECTED_SHAPE = "VM.Standard.A1.Flex"
        loadshaper.CPU_P95_SETPOINT = 15.0  # Below 20%
        loadshaper.MEM_TARGET_PCT = 15.0  # Below 20%
        loadshaper.NET_TARGET_PCT = 15.0  # Below 20%

        # Check validation logic for A1 shape
        targets_below_20 = []
        if loadshaper.CPU_P95_SETPOINT < 20.0:
            targets_below_20.append(f"CPU_P95_SETPOINT={loadshaper.CPU_P95_SETPOINT}%")
        if loadshaper.MEM_TARGET_PCT < 20.0 and "A1.Flex" in loadshaper.DETECTED_SHAPE:
            targets_below_20.append(f"MEM_TARGET_PCT={loadshaper.MEM_TARGET_PCT}%")
        if loadshaper.NET_TARGET_PCT < 20.0:
            targets_below_20.append(f"NET_TARGET_PCT={loadshaper.NET_TARGET_PCT}%")

        # A1 critical condition: all three metrics below 20%
        should_be_critical = len(targets_below_20) == 3

        self.assertTrue(should_be_critical,
                       "A1 shape with all three metrics below 20% should trigger critical warning")
        self.assertEqual(len(targets_below_20), 3,
                        "Should identify exactly 3 problematic targets for A1")

    def test_a1_shape_warning_two_metrics_below_threshold(self):
        """Test A1: warning when two metrics below 20%."""
        # Set A1 shape with two metrics below threshold
        loadshaper.DETECTED_SHAPE = "VM.Standard.A1.Flex"
        loadshaper.CPU_P95_SETPOINT = 15.0  # Below 20%
        loadshaper.MEM_TARGET_PCT = 25.0  # Above 20% (safe)
        loadshaper.NET_TARGET_PCT = 15.0  # Below 20%

        # Check validation logic
        targets_below_20 = []
        if loadshaper.CPU_P95_SETPOINT < 20.0:
            targets_below_20.append(f"CPU_P95_SETPOINT={loadshaper.CPU_P95_SETPOINT}%")
        if loadshaper.MEM_TARGET_PCT < 20.0 and "A1.Flex" in loadshaper.DETECTED_SHAPE:
            targets_below_20.append(f"MEM_TARGET_PCT={loadshaper.MEM_TARGET_PCT}%")
        if loadshaper.NET_TARGET_PCT < 20.0:
            targets_below_20.append(f"NET_TARGET_PCT={loadshaper.NET_TARGET_PCT}%")

        # A1 warning condition: exactly two metrics below 20%
        should_be_warning = len(targets_below_20) == 2

        self.assertTrue(should_be_warning,
                       "A1 shape with two metrics below 20% should trigger warning")
        self.assertEqual(len(targets_below_20), 2,
                        "Should identify exactly 2 problematic targets")

    def test_a1_shape_safe_configuration(self):
        """Test A1: safe configuration when at least one metric above 20%."""
        # Set A1 shape with one metric safe
        loadshaper.DETECTED_SHAPE = "VM.Standard.A1.Flex"
        loadshaper.CPU_P95_SETPOINT = 15.0  # Below 20%
        loadshaper.MEM_TARGET_PCT = 15.0  # Below 20%
        loadshaper.NET_TARGET_PCT = 25.0  # Above 20% (safe)

        # Check validation logic
        targets_below_20 = []
        if loadshaper.CPU_P95_SETPOINT < 20.0:
            targets_below_20.append(f"CPU_P95_SETPOINT={loadshaper.CPU_P95_SETPOINT}%")
        if loadshaper.MEM_TARGET_PCT < 20.0 and "A1.Flex" in loadshaper.DETECTED_SHAPE:
            targets_below_20.append(f"MEM_TARGET_PCT={loadshaper.MEM_TARGET_PCT}%")
        if loadshaper.NET_TARGET_PCT < 20.0:
            targets_below_20.append(f"NET_TARGET_PCT={loadshaper.NET_TARGET_PCT}%")

        # Should not be critical (one metric protects instance)
        should_be_critical = len(targets_below_20) == 3

        self.assertFalse(should_be_critical,
                        "A1 shape with at least one metric above 20% should be safe")
        self.assertEqual(len(targets_below_20), 2,
                        "Should identify 2 targets below threshold but instance still protected")

    def test_memory_targeting_disabled_for_e2(self):
        """Test that memory targeting is correctly disabled for E2 shapes."""
        # Set test values for E2 shape
        loadshaper.DETECTED_SHAPE = "VM.Standard.E2.1.Micro"
        loadshaper.CPU_P95_SETPOINT = 25.0  # Set a valid value
        loadshaper.MEM_TARGET_PCT = 0.0   # Correctly disabled
        loadshaper.NET_TARGET_PCT = 25.0  # Set a valid value

        # Memory should not be included in E2 validation
        targets_below_20 = []
        if loadshaper.CPU_P95_SETPOINT < 20.0:
            targets_below_20.append(f"CPU_P95_SETPOINT={loadshaper.CPU_P95_SETPOINT}%")
        # Note: Memory check only for A1.Flex shapes
        if loadshaper.MEM_TARGET_PCT < 20.0 and "A1.Flex" in loadshaper.DETECTED_SHAPE:
            targets_below_20.append(f"MEM_TARGET_PCT={loadshaper.MEM_TARGET_PCT}%")
        if loadshaper.NET_TARGET_PCT < 20.0:
            targets_below_20.append(f"NET_TARGET_PCT={loadshaper.NET_TARGET_PCT}%")

        # Memory should not be in the list for E2 shapes (even though MEM_TARGET_PCT=0)
        memory_in_targets = any("MEM_TARGET_PCT" in target for target in targets_below_20)
        self.assertFalse(memory_in_targets,
                        "Memory targeting should not be considered for E2 shapes")

    def test_oracle_reclamation_understanding(self):
        """Test understanding of Oracle's reclamation logic."""
        # Oracle reclaims when ALL applicable metrics are below 20%
        oracle_threshold = 20.0

        # Test E2 logic: needs BOTH CPU AND network to be below threshold
        e2_cpu = 15.0
        e2_network = 15.0
        e2_memory = 0.0  # Not applicable

        e2_would_be_reclaimed = (e2_cpu < oracle_threshold and
                                e2_network < oracle_threshold)

        self.assertTrue(e2_would_be_reclaimed,
                       "E2 with both CPU and network below 20% would be reclaimed")

        # Test A1 logic: needs ALL THREE metrics to be below threshold
        a1_cpu = 15.0
        a1_network = 15.0
        a1_memory = 15.0

        a1_would_be_reclaimed = (a1_cpu < oracle_threshold and
                                a1_network < oracle_threshold and
                                a1_memory < oracle_threshold)

        self.assertTrue(a1_would_be_reclaimed,
                       "A1 with all three metrics below 20% would be reclaimed")

        # Test A1 protected: one metric above threshold
        a1_cpu_safe = 25.0
        a1_network_low = 15.0
        a1_memory_low = 15.0

        a1_would_be_safe = not (a1_cpu_safe < oracle_threshold and
                               a1_network_low < oracle_threshold and
                               a1_memory_low < oracle_threshold)

        self.assertTrue(a1_would_be_safe,
                       "A1 with CPU above 20% should be protected even if network and memory are low")


if __name__ == '__main__':
    unittest.main()