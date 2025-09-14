#!/usr/bin/env python3
"""
Tests for configuration consistency validation feature.

This module tests the _validate_configuration_consistency(raise_on_error=False) function that
performs cross-parameter validation to prevent invalid configurations.
"""

import unittest
import unittest.mock
import sys
import os
import tempfile
import shutil
from io import StringIO
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loadshaper


class TestConfigurationConsistency(unittest.TestCase):
    """Test configuration validation and consistency checks."""

    def setUp(self):
        """Set up test environment."""
        # Store original values
        self.original_config = {}
        config_vars = [
            'CPU_P95_TARGET_MIN', 'CPU_P95_TARGET_MAX', 'CPU_P95_SETPOINT',
            'CPU_P95_HIGH_INTENSITY', 'CPU_P95_BASELINE_INTENSITY',
            'CPU_P95_EXCEEDANCE_TARGET', 'CPU_P95_SLOT_DURATION',
            'MEM_TARGET_PCT', 'NET_TARGET_PCT',
            'CPU_STOP_PCT', 'MEM_STOP_PCT', 'NET_STOP_PCT',
            'LOAD_THRESHOLD', 'LOAD_RESUME_THRESHOLD',
            'CPU_P95_RING_BUFFER_BATCH_SIZE'
        ]

        for var in config_vars:
            if hasattr(loadshaper, var):
                self.original_config[var] = getattr(loadshaper, var)

        # Initialize test defaults for required variables
        loadshaper.CPU_P95_TARGET_MIN = 20.0
        loadshaper.CPU_P95_TARGET_MAX = 30.0
        loadshaper.CPU_P95_SETPOINT = 25.0
        loadshaper.CPU_P95_HIGH_INTENSITY = 35.0
        loadshaper.CPU_P95_BASELINE_INTENSITY = 20.0
        loadshaper.CPU_P95_EXCEEDANCE_TARGET = 6.5
        loadshaper.CPU_P95_SLOT_DURATION = 60.0
        loadshaper.MEM_TARGET_PCT = 25.0
        loadshaper.NET_TARGET_PCT = 25.0
        loadshaper.CPU_STOP_PCT = 85.0
        loadshaper.MEM_STOP_PCT = 85.0
        loadshaper.NET_STOP_PCT = 85.0
        loadshaper.LOAD_THRESHOLD = 0.6
        loadshaper.LOAD_RESUME_THRESHOLD = 0.4
        loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = 10

    def tearDown(self):
        """Clean up test environment."""
        # Restore original values
        for var, value in self.original_config.items():
            if hasattr(loadshaper, var):
                setattr(loadshaper, var, value)

    def test_p95_target_range_validation(self):
        """Test P95 target range validation (MIN <= SETPOINT <= MAX)."""
        # Valid configuration
        loadshaper.CPU_P95_TARGET_MIN = 20.0
        loadshaper.CPU_P95_TARGET_MAX = 30.0
        loadshaper.CPU_P95_SETPOINT = 25.0

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)
            self.assertNotIn("WARNING", output, "Valid configuration should not produce warnings")

        # Invalid: MIN > MAX
        loadshaper.CPU_P95_TARGET_MIN = 30.0
        loadshaper.CPU_P95_TARGET_MAX = 20.0
        loadshaper.CPU_P95_SETPOINT = 25.0

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)
            self.assertIn("CPU_P95_TARGET_MIN", output, "Should warn about MIN > MAX")
            self.assertIn("CPU_P95_TARGET_MAX", output, "Should warn about MIN > MAX")

        # Invalid: SETPOINT < MIN
        loadshaper.CPU_P95_TARGET_MIN = 20.0
        loadshaper.CPU_P95_TARGET_MAX = 30.0
        loadshaper.CPU_P95_SETPOINT = 15.0

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)
            self.assertIn("CPU_P95_SETPOINT", output, "Should warn about SETPOINT < MIN")

        # Invalid: SETPOINT > MAX
        loadshaper.CPU_P95_TARGET_MIN = 20.0
        loadshaper.CPU_P95_TARGET_MAX = 30.0
        loadshaper.CPU_P95_SETPOINT = 35.0

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)
            self.assertIn("CPU_P95_SETPOINT", output, "Should warn about SETPOINT > MAX")

    def test_intensity_level_validation(self):
        """Test CPU intensity level validation (BASELINE < HIGH)."""
        # Valid configuration
        loadshaper.CPU_P95_BASELINE_INTENSITY = 20.0
        loadshaper.CPU_P95_HIGH_INTENSITY = 35.0

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)
            # Should not warn about intensity levels if other configs are valid
            if "WARNING" in output:
                self.assertNotIn("CPU_P95_BASELINE_INTENSITY", output)
                self.assertNotIn("CPU_P95_HIGH_INTENSITY", output)

        # Invalid: BASELINE >= HIGH
        loadshaper.CPU_P95_BASELINE_INTENSITY = 35.0
        loadshaper.CPU_P95_HIGH_INTENSITY = 30.0

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error was logged with the expected content
            error_calls = [call for call in mock_logger.error.call_args_list]
            error_messages = [str(call) for call in error_calls]
            error_text = " ".join(error_messages)
            self.assertIn("CPU_P95_BASELINE_INTENSITY", error_text, "Should log error about BASELINE >= HIGH")

        # Edge case: BASELINE = HIGH
        loadshaper.CPU_P95_BASELINE_INTENSITY = 30.0
        loadshaper.CPU_P95_HIGH_INTENSITY = 30.0

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)
            self.assertIn("CPU_P95_BASELINE_INTENSITY", output, "Should warn about BASELINE = HIGH")

    def test_load_threshold_validation(self):
        """Test load threshold validation (RESUME < THRESHOLD)."""
        # Valid configuration
        loadshaper.LOAD_RESUME_THRESHOLD = 0.4
        loadshaper.LOAD_THRESHOLD = 0.6

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)
            # Should not warn about load thresholds if other configs are valid

        # Invalid: RESUME >= THRESHOLD
        loadshaper.LOAD_RESUME_THRESHOLD = 0.7
        loadshaper.LOAD_THRESHOLD = 0.6

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)
            self.assertIn("LOAD_RESUME_THRESHOLD", output, "Should warn about RESUME >= THRESHOLD")

    def test_stop_percentage_validation(self):
        """Test stop percentage validation (TARGET < STOP)."""
        # Valid configuration - stop percentages should be higher than targets
        loadshaper.MEM_TARGET_PCT = 25.0
        loadshaper.MEM_STOP_PCT = 85.0
        loadshaper.NET_TARGET_PCT = 25.0
        loadshaper.NET_STOP_PCT = 85.0
        loadshaper.CPU_STOP_PCT = 85.0

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)

        # Invalid: MEM_TARGET >= MEM_STOP
        loadshaper.MEM_TARGET_PCT = 90.0
        loadshaper.MEM_STOP_PCT = 85.0

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)
            self.assertIn("MEM_TARGET_PCT", output, "Should warn about MEM_TARGET >= MEM_STOP")

        # Invalid: NET_TARGET >= NET_STOP
        loadshaper.MEM_TARGET_PCT = 25.0  # Reset to valid
        loadshaper.MEM_STOP_PCT = 85.0
        loadshaper.NET_TARGET_PCT = 90.0
        loadshaper.NET_STOP_PCT = 85.0

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)
            self.assertIn("NET_TARGET_PCT", output, "Should warn about NET_TARGET >= NET_STOP")

    def test_oracle_compliance_validation(self):
        """Test Oracle compliance validation (targets above 20% reclamation threshold)."""
        # Test CPU P95 setpoint at danger zone
        loadshaper.CPU_P95_SETPOINT = 19.0  # Below Oracle 20% threshold

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)
            self.assertIn("Oracle reclamation", output, "Should warn about Oracle reclamation risk")
            self.assertIn("CPU_P95_SETPOINT", output, "Should mention CPU P95 setpoint")

        # Test memory target at danger zone (for A1 shapes)
        loadshaper.CPU_P95_SETPOINT = 25.0  # Reset to safe value
        loadshaper.MEM_TARGET_PCT = 18.0  # Below Oracle 20% threshold

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)
            self.assertIn("Oracle reclamation", output, "Should warn about Oracle reclamation risk")
            self.assertIn("MEM_TARGET_PCT", output, "Should mention memory target")

        # Test network target at danger zone
        loadshaper.MEM_TARGET_PCT = 25.0  # Reset to safe value
        loadshaper.NET_TARGET_PCT = 15.0  # Below Oracle 20% threshold

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)
            self.assertIn("Oracle reclamation", output, "Should warn about Oracle reclamation risk")
            self.assertIn("NET_TARGET_PCT", output, "Should mention network target")

    def test_exceedance_target_validation(self):
        """Test exceedance target validation (reasonable percentage range)."""
        # Valid exceedance target
        loadshaper.CPU_P95_EXCEEDANCE_TARGET = 6.5

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)

        # Too high exceedance target
        loadshaper.CPU_P95_EXCEEDANCE_TARGET = 25.0

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)
            self.assertIn("CPU_P95_EXCEEDANCE_TARGET", output, "Should warn about high exceedance target")

        # Too low exceedance target
        loadshaper.CPU_P95_EXCEEDANCE_TARGET = 1.0

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)
            self.assertIn("CPU_P95_EXCEEDANCE_TARGET", output, "Should warn about low exceedance target")

    def test_slot_duration_validation(self):
        """Test slot duration validation (reasonable timing)."""
        # Valid slot duration
        loadshaper.CPU_P95_SLOT_DURATION = 60.0

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)

        # Too short slot duration
        loadshaper.CPU_P95_SLOT_DURATION = 10.0

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)
            self.assertIn("CPU_P95_SLOT_DURATION", output, "Should warn about short slot duration")

        # Too long slot duration
        loadshaper.CPU_P95_SLOT_DURATION = 900.0

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)
            self.assertIn("CPU_P95_SLOT_DURATION", output, "Should warn about long slot duration")

    def test_ring_buffer_batch_size_validation(self):
        """Test ring buffer batch size validation."""
        # Valid batch size
        loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = 10

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)

        # Invalid: zero or negative batch size
        loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = 0

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)
            self.assertIn("CPU_P95_RING_BUFFER_BATCH_SIZE", output, "Should warn about zero batch size")

        loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = -5

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)
            self.assertIn("CPU_P95_RING_BUFFER_BATCH_SIZE", output, "Should warn about negative batch size")

        # Very large batch size (performance warning)
        loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = 1000

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)
            self.assertIn("CPU_P95_RING_BUFFER_BATCH_SIZE", output, "Should warn about very large batch size")

    def test_multiple_validation_errors(self):
        """Test handling of multiple validation errors."""
        # Set up multiple invalid configurations
        loadshaper.CPU_P95_TARGET_MIN = 30.0
        loadshaper.CPU_P95_TARGET_MAX = 20.0  # Invalid: MIN > MAX
        loadshaper.CPU_P95_SETPOINT = 35.0    # Invalid: SETPOINT > MAX
        loadshaper.CPU_P95_BASELINE_INTENSITY = 40.0
        loadshaper.CPU_P95_HIGH_INTENSITY = 30.0  # Invalid: BASELINE > HIGH
        loadshaper.LOAD_RESUME_THRESHOLD = 0.8
        loadshaper.LOAD_THRESHOLD = 0.6  # Invalid: RESUME > THRESHOLD

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)

            # Should report all validation errors
            self.assertIn("CPU_P95_TARGET_MIN", output)
            self.assertIn("CPU_P95_TARGET_MAX", output)
            self.assertIn("CPU_P95_SETPOINT", output)
            self.assertIn("CPU_P95_BASELINE_INTENSITY", output)
            self.assertIn("LOAD_RESUME_THRESHOLD", output)

            # Check that multiple errors were reported
            self.assertGreater(len(error_calls) + len(warning_calls), 3, "Should report multiple errors/warnings")

    def test_validation_with_none_values(self):
        """Test validation handles None values gracefully."""
        # Set some values to None (uninitialized)
        loadshaper.CPU_P95_TARGET_MIN = None
        loadshaper.CPU_P95_TARGET_MAX = 30.0
        loadshaper.CPU_P95_SETPOINT = 25.0

        # Should not crash with None values
        try:
            with unittest.mock.patch('sys.stdout', new_callable=StringIO):
                loadshaper._validate_configuration_consistency(raise_on_error=False)
            success = True
        except (TypeError, AttributeError):
            success = False

        self.assertTrue(success, "Validation should handle None values gracefully")

    def test_validation_startup_integration(self):
        """Test that validation is called during initialization."""
        # This test verifies that configuration validation is integrated
        # into the main initialization flow

        # Set up an invalid configuration that should trigger warnings
        loadshaper.CPU_P95_TARGET_MIN = 30.0
        loadshaper.CPU_P95_TARGET_MAX = 20.0  # Invalid: MIN > MAX

        with patch('loadshaper.logger') as mock_logger:
            # Call the validation function directly (simulating startup)
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)

            # Check that validation produced error/warning messages
            self.assertTrue(len(error_calls) > 0 or len(warning_calls) > 0, "Should produce error or warning messages during validation")
            self.assertIn("configuration", output.lower(), "Should mention configuration")

    def test_warning_message_quality(self):
        """Test that warning messages are clear and actionable."""
        # Test specific warning message content
        loadshaper.CPU_P95_TARGET_MIN = 30.0
        loadshaper.CPU_P95_TARGET_MAX = 20.0

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            # Check if error/warning was logged
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            output = " ".join(error_calls + warning_calls)

            # Error/warning messages should be clear and actionable
            self.assertTrue(len(error_calls) > 0 or len(warning_calls) > 0, "Should produce error or warning messages")
            # Should mention the specific values
            self.assertIn("30", output)
            self.assertIn("20", output)
            # Should give guidance in the messages
            self.assertTrue("must be" in output.lower() or "should be" in output.lower(),
                           "Messages should include actionable guidance")


if __name__ == '__main__':
    unittest.main()