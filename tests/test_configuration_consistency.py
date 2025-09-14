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
            'CPU_P95_RING_BUFFER_BATCH_SIZE', 'CONTROL_PERIOD', 'AVG_WINDOW_SEC',
            'NET_FALLBACK_START_PCT', 'NET_FALLBACK_STOP_PCT', 'MEM_MIN_FREE_MB'
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
        loadshaper.CONTROL_PERIOD = 5.0
        loadshaper.AVG_WINDOW_SEC = 300.0
        loadshaper.NET_FALLBACK_START_PCT = 19.0
        loadshaper.NET_FALLBACK_STOP_PCT = 23.0
        loadshaper.MEM_MIN_FREE_MB = 512

    def tearDown(self):
        """Clean up test environment."""
        # Restore original values
        for var, value in self.original_config.items():
            if hasattr(loadshaper, var):
                setattr(loadshaper, var, value)

    def test_p95_target_range_validation(self):
        """P95 target range: MIN <= SETPOINT <= MAX."""
        # Valid configuration
        loadshaper.CPU_P95_TARGET_MIN = 20.0
        loadshaper.CPU_P95_TARGET_MAX = 30.0
        loadshaper.CPU_P95_SETPOINT = 25.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            output = " ".join([str(c) for c in (mock_logger.error.call_args_list + mock_logger.warning.call_args_list)])
            self.assertNotIn("WARNING", output)

        # Invalid: MIN > MAX
        loadshaper.CPU_P95_TARGET_MIN = 30.0
        loadshaper.CPU_P95_TARGET_MAX = 20.0
        loadshaper.CPU_P95_SETPOINT = 25.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            output = " ".join([str(c) for c in (mock_logger.error.call_args_list + mock_logger.warning.call_args_list)])
            self.assertIn("CPU_P95_TARGET_MIN", output)
            self.assertIn("CPU_P95_TARGET_MAX", output)

        # Invalid: SETPOINT < MIN
        loadshaper.CPU_P95_TARGET_MIN = 20.0
        loadshaper.CPU_P95_TARGET_MAX = 30.0
        loadshaper.CPU_P95_SETPOINT = 15.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            output = " ".join([str(c) for c in (mock_logger.error.call_args_list + mock_logger.warning.call_args_list)])
            self.assertIn("CPU_P95_SETPOINT", output)

        # Invalid: SETPOINT > MAX
        loadshaper.CPU_P95_SETPOINT = 35.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            output = " ".join([str(c) for c in (mock_logger.error.call_args_list + mock_logger.warning.call_args_list)])
            self.assertIn("CPU_P95_SETPOINT", output)

    def test_intensity_level_validation(self):
        """CPU intensity: BASELINE < HIGH."""
        # Valid configuration
        loadshaper.CPU_P95_BASELINE_INTENSITY = 20.0
        loadshaper.CPU_P95_HIGH_INTENSITY = 35.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            output = " ".join([str(c) for c in (mock_logger.error.call_args_list + mock_logger.warning.call_args_list)])
            if "WARNING" in output:
                self.assertNotIn("CPU_P95_BASELINE_INTENSITY", output)
                self.assertNotIn("CPU_P95_HIGH_INTENSITY", output)

        # Invalid: BASELINE >= HIGH
        loadshaper.CPU_P95_BASELINE_INTENSITY = 35.0
        loadshaper.CPU_P95_HIGH_INTENSITY = 30.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            error_text = " ".join([str(c) for c in mock_logger.error.call_args_list])
            self.assertIn("CPU_P95_BASELINE_INTENSITY", error_text)

        # Edge case: BASELINE = HIGH
        loadshaper.CPU_P95_BASELINE_INTENSITY = 30.0
        loadshaper.CPU_P95_HIGH_INTENSITY = 30.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            output = " ".join([str(c) for c in (mock_logger.error.call_args_list + mock_logger.warning.call_args_list)])
            self.assertIn("CPU_P95_BASELINE_INTENSITY", output)

    def test_load_threshold_validation(self):
        """Load thresholds: RESUME < THRESHOLD."""
        # Valid configuration
        loadshaper.LOAD_RESUME_THRESHOLD = 0.4
        loadshaper.LOAD_THRESHOLD = 0.6
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)

        # Invalid: RESUME >= THRESHOLD
        loadshaper.LOAD_RESUME_THRESHOLD = 0.7
        loadshaper.LOAD_THRESHOLD = 0.6
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            output = " ".join([str(c) for c in (mock_logger.error.call_args_list + mock_logger.warning.call_args_list)])
            self.assertIn("LOAD_RESUME_THRESHOLD", output)

    def test_stop_percentage_validation(self):
        """Stop percentages: TARGET < STOP."""
        # Valid configuration
        loadshaper.MEM_TARGET_PCT = 25.0
        loadshaper.MEM_STOP_PCT = 85.0
        loadshaper.NET_TARGET_PCT = 25.0
        loadshaper.NET_STOP_PCT = 85.0
        loadshaper.CPU_STOP_PCT = 85.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)

        # Invalid: MEM_TARGET >= MEM_STOP
        loadshaper.MEM_TARGET_PCT = 90.0
        loadshaper.MEM_STOP_PCT = 85.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            output = " ".join([str(c) for c in (mock_logger.error.call_args_list + mock_logger.warning.call_args_list)])
            self.assertIn("MEM_TARGET_PCT", output)

        # Reset MEM to valid, test NET invalid
        loadshaper.MEM_TARGET_PCT = 25.0
        loadshaper.MEM_STOP_PCT = 85.0
        loadshaper.NET_TARGET_PCT = 90.0
        loadshaper.NET_STOP_PCT = 85.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            output = " ".join([str(c) for c in (mock_logger.error.call_args_list + mock_logger.warning.call_args_list)])
            self.assertIn("NET_TARGET_PCT", output)

    def test_oracle_compliance_validation(self):
        """Oracle 20% reclamation threshold warns/errors appropriately."""
        # CPU P95 target min warning content
        loadshaper.CPU_P95_TARGET_MIN = 19.0  # Below Oracle 20% threshold
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            warnings = " ".join([str(c) for c in mock_logger.warning.call_args_list])
            self.assertIn("Oracle 20% reclamation threshold", warnings)

        # CPU P95 setpoint below threshold -> warning
        loadshaper.CPU_P95_TARGET_MIN = 20.0
        loadshaper.CPU_P95_SETPOINT = 19.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            warnings = " ".join([str(c) for c in mock_logger.warning.call_args_list])
            self.assertIn("Oracle reclamation", warnings)

        # Memory below threshold -> warning
        loadshaper.CPU_P95_SETPOINT = 25.0
        loadshaper.MEM_TARGET_PCT = 19.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            warnings = " ".join([str(c) for c in mock_logger.warning.call_args_list])
            self.assertIn("MEM_TARGET_PCT", warnings)
            self.assertIn("Oracle reclamation", warnings)

        # Network below threshold -> warning
        loadshaper.MEM_TARGET_PCT = 25.0
        loadshaper.NET_TARGET_PCT = 19.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            warnings = " ".join([str(c) for c in mock_logger.warning.call_args_list])
            self.assertIn("NET_TARGET_PCT", warnings)
            self.assertIn("Oracle reclamation", warnings)

        # Two below 20% -> combined warning
        loadshaper.NET_TARGET_PCT = 18.0
        loadshaper.MEM_TARGET_PCT = 19.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            combined = [c for c in mock_logger.warning.call_args_list if 'below 20% threshold' in str(c)]
            self.assertTrue(len(combined) > 0)

        # All three below 20% -> error
        loadshaper.CPU_P95_TARGET_MIN = 19.0
        loadshaper.MEM_TARGET_PCT = 18.0
        loadshaper.NET_TARGET_PCT = 15.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            errors = " ".join([str(c) for c in mock_logger.error.call_args_list])
            self.assertIn("Oracle", errors)
            self.assertIn("reclamation", errors)

    def test_timing_relationships_validation(self):
        """Timing relationships and exceedance target bounds."""
        # Valid configuration
        loadshaper.CONTROL_PERIOD = 5.0
        loadshaper.AVG_WINDOW_SEC = 300.0
        loadshaper.CPU_P95_SLOT_DURATION = 60.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)

        # AVG window too short -> warning
        loadshaper.AVG_WINDOW_SEC = 40.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            warnings = " ".join([str(c) for c in mock_logger.warning.call_args_list])
            self.assertIn("AVG_WINDOW_SEC", warnings)

        # Slot duration too short -> warning
        loadshaper.AVG_WINDOW_SEC = 300.0
        loadshaper.CPU_P95_SLOT_DURATION = 20.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            warnings = " ".join([str(c) for c in mock_logger.warning.call_args_list])
            self.assertIn("CPU_P95_SLOT_DURATION", warnings)

        # Exceedance target too high -> warning
        loadshaper.CPU_P95_EXCEEDANCE_TARGET = 25.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            warnings = " ".join([str(c) for c in mock_logger.warning.call_args_list])
            self.assertIn("CPU_P95_EXCEEDANCE_TARGET", warnings)

        # Exceedance target too low -> warning
        loadshaper.CPU_P95_EXCEEDANCE_TARGET = 1.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            warnings = " ".join([str(c) for c in mock_logger.warning.call_args_list])
            self.assertIn("CPU_P95_EXCEEDANCE_TARGET", warnings)

    def test_slot_duration_validation(self):
        """Slot duration extreme bounds warnings."""
        # Valid slot duration
        loadshaper.CPU_P95_SLOT_DURATION = 60.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)

        # Too short slot duration
        loadshaper.CPU_P95_SLOT_DURATION = 10.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            warnings = " ".join([str(c) for c in mock_logger.warning.call_args_list])
            self.assertIn("CPU_P95_SLOT_DURATION_SEC", warnings)

        # Too long slot duration
        loadshaper.CPU_P95_SLOT_DURATION = 900.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            warnings = " ".join([str(c) for c in mock_logger.warning.call_args_list])
            self.assertIn("CPU_P95_SLOT_DURATION_SEC", warnings)

    def test_ring_buffer_batch_size_validation(self):
        """Ring buffer batch size validation and warnings."""
        # Valid batch size
        loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = 10
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)

        # Zero batch size -> warning
        loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = 0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            warnings = " ".join([str(c) for c in mock_logger.warning.call_args_list])
            self.assertIn("CPU_P95_RING_BUFFER_BATCH_SIZE", warnings)

        # Negative batch size -> warning
        loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = -5
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            warnings = " ".join([str(c) for c in mock_logger.warning.call_args_list])
            self.assertIn("CPU_P95_RING_BUFFER_BATCH_SIZE", warnings)

        # Very large batch size -> warning
        loadshaper.CPU_P95_RING_BUFFER_BATCH_SIZE = 1000
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            warnings = " ".join([str(c) for c in mock_logger.warning.call_args_list])
            self.assertIn("CPU_P95_RING_BUFFER_BATCH_SIZE", warnings)

    def test_multiple_validation_errors(self):
        """Multiple invalid settings produce multiple errors."""
        # Set up multiple invalid configurations
        loadshaper.CPU_P95_TARGET_MIN = 30.0
        loadshaper.CPU_P95_TARGET_MAX = 20.0  # Invalid: MIN > MAX
        loadshaper.CPU_P95_SETPOINT = 35.0    # Invalid: SETPOINT > MAX
        loadshaper.CPU_P95_BASELINE_INTENSITY = 40.0
        loadshaper.CPU_P95_HIGH_INTENSITY = 30.0  # Invalid: BASELINE > HIGH
        loadshaper.LOAD_RESUME_THRESHOLD = 0.8
        loadshaper.LOAD_THRESHOLD = 0.6       # Invalid: RESUME > THRESHOLD

        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            error_count = len(mock_logger.error.call_args_list)
            warning_count = len(mock_logger.warning.call_args_list)
            # Keep HEAD's assertion semantics (expect 4 errors)
            self.assertEqual(error_count, 4)
            self.assertGreaterEqual(error_count + warning_count, 4)

    def test_exceedance_target_validation(self):
        """Exceedance target out-of-range should only warn."""
        # High exceedance target
        loadshaper.CPU_P95_EXCEEDANCE_TARGET = 25.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            warnings = " ".join([str(c) for c in mock_logger.warning.call_args_list])
            self.assertIn("CPU_P95_EXCEEDANCE_TARGET", warnings)

        # Low exceedance target
        loadshaper.CPU_P95_EXCEEDANCE_TARGET = 1.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            warnings = " ".join([str(c) for c in mock_logger.warning.call_args_list])
            self.assertIn("CPU_P95_EXCEEDANCE_TARGET", warnings)

    def test_validation_with_none_values(self):
        """Validation handles None values gracefully."""
        loadshaper.CPU_P95_TARGET_MIN = None
        loadshaper.CPU_P95_TARGET_MAX = 30.0
        loadshaper.CPU_P95_SETPOINT = 25.0
        try:
            with unittest.mock.patch('sys.stdout', new_callable=StringIO):
                loadshaper._validate_configuration_consistency(raise_on_error=False)
            success = True
        except (TypeError, AttributeError):
            success = False
        self.assertTrue(success)

    def test_validation_startup_integration(self):
        """Validation produces messages during initialization with bad config."""
        loadshaper.CPU_P95_TARGET_MIN = 30.0
        loadshaper.CPU_P95_TARGET_MAX = 20.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            self.assertTrue(len(mock_logger.error.call_args_list) > 0 or len(mock_logger.warning.call_args_list) > 0)
            output = " ".join([str(c) for c in (mock_logger.error.call_args_list + mock_logger.warning.call_args_list)])
            self.assertIn("configuration", output.lower())

    def test_warning_message_quality_oracle_value_included(self):
        """Warning messages include concrete values (e.g., 19.0)."""
        loadshaper.CPU_P95_TARGET_MIN = 19.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            if mock_logger.warning.call_args_list:
                warning_text = str(mock_logger.warning.call_args_list[0])
                self.assertIn("19.0", warning_text)

    def test_warning_message_quality_actionable(self):
        """Warning/error messages are clear and actionable."""
        loadshaper.CPU_P95_TARGET_MIN = 30.0
        loadshaper.CPU_P95_TARGET_MAX = 20.0
        with patch('loadshaper.logger') as mock_logger:
            loadshaper._validate_configuration_consistency(raise_on_error=False)
            output = " ".join([str(c) for c in (mock_logger.error.call_args_list + mock_logger.warning.call_args_list)])
            self.assertTrue(len(mock_logger.error.call_args_list) > 0 or len(mock_logger.warning.call_args_list) > 0)
            self.assertIn("30", output)
            self.assertIn("20", output)
            self.assertTrue("must be" in output.lower() or "should be" in output.lower())


if __name__ == '__main__':
    unittest.main()

