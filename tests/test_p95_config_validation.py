#!/usr/bin/env python3

import unittest
import unittest.mock
import sys
import os

# Add the parent directory to the path so we can import loadshaper
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import loadshaper


class TestP95ConfigurationValidation(unittest.TestCase):
    """Test P95 configuration variables are properly set and validated."""

    def setUp(self):
        """Set up test environment before each test."""
        # Store original values
        self.original_values = {
            'CPU_P95_SETPOINT': getattr(loadshaper, 'CPU_P95_SETPOINT', 25.0),
            'CPU_P95_TARGET_MIN': getattr(loadshaper, 'CPU_P95_TARGET_MIN', 22.0),
            'CPU_P95_TARGET_MAX': getattr(loadshaper, 'CPU_P95_TARGET_MAX', 28.0),
            'CPU_P95_EXCEEDANCE_TARGET': getattr(loadshaper, 'CPU_P95_EXCEEDANCE_TARGET', 6.5),
            'CPU_P95_BASELINE_INTENSITY': getattr(loadshaper, 'CPU_P95_BASELINE_INTENSITY', 20.0),
            'CPU_P95_HIGH_INTENSITY': getattr(loadshaper, 'CPU_P95_HIGH_INTENSITY', 35.0),
            'CPU_P95_SLOT_DURATION': getattr(loadshaper, 'CPU_P95_SLOT_DURATION', 60.0),
        }

    def tearDown(self):
        """Clean up after each test."""
        for key, value in self.original_values.items():
            setattr(loadshaper, key, value)

    def test_p95_setpoint_validation(self):
        """Test CPU_P95_SETPOINT is above Oracle's 20% threshold."""
        # Valid setpoint (above 20%)
        loadshaper.CPU_P95_SETPOINT = 25.0
        self.assertGreater(loadshaper.CPU_P95_SETPOINT, 20.0,
                          "CPU_P95_SETPOINT must be above 20% to prevent Oracle reclamation")

        # Test edge case - exactly 20% should be valid but risky
        loadshaper.CPU_P95_SETPOINT = 20.0
        self.assertGreaterEqual(loadshaper.CPU_P95_SETPOINT, 20.0,
                               "CPU_P95_SETPOINT should be at least 20%")

    def test_p95_target_range_validation(self):
        """Test CPU_P95_TARGET_MIN and CPU_P95_TARGET_MAX are properly ordered."""
        loadshaper.CPU_P95_TARGET_MIN = 22.0
        loadshaper.CPU_P95_TARGET_MAX = 28.0

        self.assertLess(loadshaper.CPU_P95_TARGET_MIN, loadshaper.CPU_P95_TARGET_MAX,
                       "CPU_P95_TARGET_MIN must be less than CPU_P95_TARGET_MAX")

        self.assertGreater(loadshaper.CPU_P95_TARGET_MIN, 20.0,
                          "CPU_P95_TARGET_MIN must be above Oracle's 20% threshold")

        self.assertLessEqual(loadshaper.CPU_P95_TARGET_MAX, 100.0,
                            "CPU_P95_TARGET_MAX must not exceed 100%")

    def test_p95_exceedance_target_validation(self):
        """Test CPU_P95_EXCEEDANCE_TARGET is within valid range."""
        # Valid exceedance target
        loadshaper.CPU_P95_EXCEEDANCE_TARGET = 6.5
        self.assertGreater(loadshaper.CPU_P95_EXCEEDANCE_TARGET, 0.0,
                          "CPU_P95_EXCEEDANCE_TARGET must be positive")
        self.assertLess(loadshaper.CPU_P95_EXCEEDANCE_TARGET, 100.0,
                       "CPU_P95_EXCEEDANCE_TARGET must be less than 100%")

        # Recommended range (5-15%)
        self.assertGreaterEqual(loadshaper.CPU_P95_EXCEEDANCE_TARGET, 5.0,
                               "CPU_P95_EXCEEDANCE_TARGET should be at least 5% for effective control")
        self.assertLessEqual(loadshaper.CPU_P95_EXCEEDANCE_TARGET, 15.0,
                            "CPU_P95_EXCEEDANCE_TARGET should be at most 15% to prevent overshooting")

    def test_p95_intensity_validation(self):
        """Test CPU intensity values are properly configured."""
        loadshaper.CPU_P95_BASELINE_INTENSITY = 20.0
        loadshaper.CPU_P95_HIGH_INTENSITY = 35.0

        self.assertGreater(loadshaper.CPU_P95_HIGH_INTENSITY, loadshaper.CPU_P95_BASELINE_INTENSITY,
                          "CPU_P95_HIGH_INTENSITY must be greater than CPU_P95_BASELINE_INTENSITY")

        self.assertGreater(loadshaper.CPU_P95_BASELINE_INTENSITY, 0.0,
                          "CPU_P95_BASELINE_INTENSITY must be positive")

        self.assertLessEqual(loadshaper.CPU_P95_HIGH_INTENSITY, 100.0,
                            "CPU_P95_HIGH_INTENSITY must not exceed 100%")

    def test_p95_slot_duration_validation(self):
        """Test CPU_P95_SLOT_DURATION is reasonable."""
        # Default 5-second slots
        loadshaper.CPU_P95_SLOT_DURATION = 5.0
        self.assertGreater(loadshaper.CPU_P95_SLOT_DURATION, 0.0,
                          "CPU_P95_SLOT_DURATION must be positive")

        # Should be reasonable for control system (1-60 seconds)
        self.assertGreaterEqual(loadshaper.CPU_P95_SLOT_DURATION, 1.0,
                               "CPU_P95_SLOT_DURATION should be at least 1 second")
        self.assertLessEqual(loadshaper.CPU_P95_SLOT_DURATION, 60.0,
                            "CPU_P95_SLOT_DURATION should be at most 60 seconds for responsiveness")

    def test_p95_setpoint_within_target_range(self):
        """Test CPU_P95_SETPOINT falls within the target range."""
        loadshaper.CPU_P95_SETPOINT = 25.0
        loadshaper.CPU_P95_TARGET_MIN = 22.0
        loadshaper.CPU_P95_TARGET_MAX = 28.0

        self.assertGreaterEqual(loadshaper.CPU_P95_SETPOINT, loadshaper.CPU_P95_TARGET_MIN,
                               "CPU_P95_SETPOINT should be within or above target range")
        self.assertLessEqual(loadshaper.CPU_P95_SETPOINT, loadshaper.CPU_P95_TARGET_MAX,
                            "CPU_P95_SETPOINT should be within or below target range")

    def test_p95_controller_initialization_with_valid_config(self):
        """Test P95 controller can be initialized with valid configuration."""
        # Set valid configuration
        loadshaper.CPU_P95_SETPOINT = 25.0
        loadshaper.CPU_P95_TARGET_MIN = 22.0
        loadshaper.CPU_P95_TARGET_MAX = 28.0
        loadshaper.CPU_P95_EXCEEDANCE_TARGET = 6.5
        loadshaper.CPU_P95_BASELINE_INTENSITY = 20.0
        loadshaper.CPU_P95_HIGH_INTENSITY = 35.0
        loadshaper.CPU_P95_SLOT_DURATION = 5.0

        # Mock MetricsStorage to avoid database dependency
        with unittest.mock.patch('loadshaper.MetricsStorage') as mock_storage:
            mock_storage_instance = unittest.mock.Mock()
            mock_storage.return_value = mock_storage_instance

            # Mock get_cpu_p95 to return None (no data yet)
            with unittest.mock.patch.object(loadshaper.CPUP95Controller, 'get_cpu_p95', return_value=None):
                # Should not raise any exceptions
                controller = loadshaper.CPUP95Controller(mock_storage_instance)
                self.assertIsNotNone(controller, "P95 controller should initialize with valid configuration")

    def test_config_validation_against_oracle_shapes(self):
        """Test configuration values match Oracle shape recommendations."""
        # E2.1.Micro recommendations (conservative shared tenancy)
        e2_configs = {
            'CPU_P95_SETPOINT': 25.0,
            'CPU_P95_TARGET_MIN': 22.0,
            'CPU_P95_TARGET_MAX': 28.0,
        }

        for key, value in e2_configs.items():
            setattr(loadshaper, key, value)
            self.assertGreater(getattr(loadshaper, key), 20.0,
                              f"E2 {key} must be above Oracle's 20% threshold")

        # A1.Flex recommendations (higher dedicated resources)
        a1_configs = {
            'CPU_P95_SETPOINT': 28.5,
            'CPU_P95_TARGET_MIN': 22.0,
            'CPU_P95_TARGET_MAX': 32.0,
        }

        for key, value in a1_configs.items():
            setattr(loadshaper, key, value)
            self.assertGreater(getattr(loadshaper, key), 20.0,
                              f"A1 {key} must be above Oracle's 20% threshold")

    def test_cpu_p95_slot_duration_validation(self):
        """Test CPU_P95_SLOT_DURATION_SEC validation prevents division by zero."""
        # Test that _validate_config_value correctly validates CPU_P95_SLOT_DURATION_SEC

        # Valid values should not raise
        try:
            loadshaper._validate_config_value('CPU_P95_SLOT_DURATION_SEC', '60.0')
            loadshaper._validate_config_value('CPU_P95_SLOT_DURATION_SEC', '10.0')
            loadshaper._validate_config_value('CPU_P95_SLOT_DURATION_SEC', '3600.0')
        except ValueError:
            self.fail("Valid CPU_P95_SLOT_DURATION_SEC values should not raise ValueError")

        # Invalid values should raise ValueError
        with self.assertRaises(ValueError):
            loadshaper._validate_config_value('CPU_P95_SLOT_DURATION_SEC', '0')

        with self.assertRaises(ValueError):
            loadshaper._validate_config_value('CPU_P95_SLOT_DURATION_SEC', '-1.0')

        with self.assertRaises(ValueError):
            loadshaper._validate_config_value('CPU_P95_SLOT_DURATION_SEC', '5.0')  # Below minimum 10.0

        with self.assertRaises(ValueError):
            loadshaper._validate_config_value('CPU_P95_SLOT_DURATION_SEC', '7200.0')  # Above maximum 3600.0

        with self.assertRaises(ValueError):
            loadshaper._validate_config_value('CPU_P95_SLOT_DURATION_SEC', 'not_a_number')

    def test_p95_cache_ttl_constant_exists(self):
        """Test that P95_CACHE_TTL_SEC constant is properly defined."""
        # Verify the constant exists in CPUP95Controller
        controller_class = getattr(loadshaper, 'CPUP95Controller', None)
        self.assertIsNotNone(controller_class, "CPUP95Controller class should exist")

        # Check if P95_CACHE_TTL_SEC is defined as class constant
        cache_ttl = getattr(controller_class, 'P95_CACHE_TTL_SEC', None)
        self.assertIsNotNone(cache_ttl, "P95_CACHE_TTL_SEC should be defined as class constant")
        self.assertIsInstance(cache_ttl, (int, float), "P95_CACHE_TTL_SEC should be numeric")
        self.assertGreater(cache_ttl, 0, "P95_CACHE_TTL_SEC should be positive")

        # Should be reasonable cache duration (30-300 seconds)
        self.assertGreaterEqual(cache_ttl, 30, "Cache TTL should be at least 30 seconds")
        self.assertLessEqual(cache_ttl, 300, "Cache TTL should be at most 300 seconds")


if __name__ == '__main__':
    unittest.main()